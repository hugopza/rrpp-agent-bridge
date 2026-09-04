# Desplegament a Hetzner

El desplegament de producció suportat és un únic VPS Hetzner amb Ubuntu 24.04
LTS. SQLite i la cua durable continuen sent locals. No calen Redis, PostgreSQL,
SQS ni Kubernetes.

## Topologia i superfície pública

```text
Internet :443
  -> Nginx
     -> només /webhooks/instagram
        -> 127.0.0.1:8081 (contenidor ingress)

Túnel SSH -> 127.0.0.1:8080 (contenidor dashboard)
Worker systemd -> SQLite local + OpenClaw 127.0.0.1:18789
Maintenance contenidor -> SQLite local + backups persistents
```

Nginx i OpenClaw s'executen a l'host. El worker també s'executa a l'host perquè
`OPENCLAW_BASE_URL` continuï sent estrictament loopback. Web, ingress i
manteniment s'executen amb Compose i conserven `read_only`, `cap_drop: ALL`,
`no-new-privileges`, usuari no privilegiat i `tmpfs`. Els ports 8080 i 8081 es
publiquen només a `127.0.0.1`. No s'ha de publicar mai 18789, el dashboard, el
fitxer SQLite ni cap directori de backup.

## 1. Preparar Ubuntu

Configura primer un registre DNS `A`/`AAAA` per al domini del webhook. A la
firewall de Hetzner permet només SSH des de les IP d'administració i els ports
TCP 80/443. Replica la mateixa política amb UFW:

```bash
sudo apt update
sudo apt install -y acl age ca-certificates curl git nginx python3.12-venv snapd ufw util-linux
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
```

Instal·la Docker Engine i el plugin Compose des del repositori oficial de
Docker per a Ubuntu:

```bash
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo docker version
sudo docker compose version
```

La instal·lació oficial actual es documenta a
[Docker Engine on Ubuntu](https://docs.docker.com/engine/install/ubuntu/) i
[Docker Compose plugin](https://docs.docker.com/compose/install/linux/).

## 2. Usuari, checkout i directoris persistents

El contenidor conserva la identitat no privilegiada `10001:10001` i el worker
de l'host conserva l'usuari `rrpp`. En una instal·lació nova poden coincidir,
però els directoris persistents no depenen d'aquesta coincidència: el bootstrap
aplica ACLs explícites i ACLs per defecte per a totes dues identitats. El checkout
és de root i només els directoris de dades són compartits per escriptura:

```bash
sudo groupadd --gid 10001 rrpp
sudo useradd --uid 10001 --gid 10001 --shell /usr/sbin/nologin \
  --home-dir /var/lib/rrpp-agent-bridge --create-home rrpp
sudo git clone REPOSITORY_URL /opt/rrpp-agent-bridge
cd /opt/rrpp-agent-bridge
sudo bash scripts/prepare-production-storage.sh
sudo python3.12 -m venv .venv
sudo .venv/bin/python -m pip install -e '.[deployment]'
```

`prepare-production-storage.sh` crea `var/`, `backups/` i `backup-export/`,
repara idempotentment les ACLs dels directoris i fitxers existents, configura
ACLs per defecte per als fitxers nous i comprova escriptura creuada real entre
`rrpp` i `10001:10001`. El mateix check s'executa automàticament en cada deploy,
abans de la migració. Si `acl`, `setpriv`, l'usuari host o l'accés efectiu
falten, el desplegament falla abans d'arrencar els serveis.

No situïs `var`, `backups` o `backup-export` en emmagatzematge efímer.
SQLite, els fitxers WAL/SHM i els backups han de quedar al mateix disc local; no
facis servir NFS per al directori de la base de dades.

## 3. Configuració privada

Copia la plantilla de producció i edita-la sense afegir cometes de shell:

```bash
sudo install -d -o root -g rrpp -m 0750 /etc/rrpp-agent-bridge
sudo install -o root -g rrpp -m 0640 \
  .env.production.example /etc/rrpp-agent-bridge/rrpp.env
sudoedit /etc/rrpp-agent-bridge/rrpp.env
```

Genera valors aleatoris diferents per a la contrasenya del dashboard, el secret
de sessió i el verify token. Afegeix l'App Secret i els tokens de Meta, però no
els escriguis a Git, logs, captures o documentació. Mantén `RRPP_MODE=shadow` i
`RRPP_INSTAGRAM_SEND_ENABLED=false` durant el primer desplegament.

Per a diversos comptes d'una mateixa Meta App, deixa buides les tres variables
legacy i usa `INSTAGRAM_ACCOUNTS_JSON` més una variable
`INSTAGRAM_ACCOUNT_<ALIAS>_ACCESS_TOKEN` per compte. No barregis els dos formats.

Configura obligatòriament `RRPP_BACKUP_AGE_RECIPIENT` amb una clau pública
`age`. La identitat privada corresponent no ha d'existir al VPS.

## 4. OpenClaw privat

Instal·la OpenClaw per a l'usuari `rrpp` seguint la documentació del projecte i
configura el Gateway amb `gateway.bind=loopback`, autenticació per token i el
port 18789. La documentació actual confirma que `loopback` és el bind per
defecte i que els binds no-loopback amplien explícitament la superfície de xarxa:
[OpenClaw Gateway](https://docs.openclaw.ai/gateway) i
[configuration reference](https://docs.openclaw.ai/gateway/configuration-reference).

Instal·la o repara el servei gestionat pel mateix OpenClaw i permet que el servei
d'usuari arrenqui sense sessió interactiva:

```bash
sudo loginctl enable-linger rrpp
sudo -u rrpp -H openclaw gateway install --force --port 18789
sudo -u rrpp -H openclaw gateway status --deep
sudo ss -lntp | grep 18789
```

El listener ha de ser `127.0.0.1:18789` o `::1:18789`, mai `0.0.0.0`.
Copia el mateix token al fitxer d'entorn com `OPENCLAW_GATEWAY_TOKEN`. Mantén
l'agent `rrpp` sense eines, bindings de canal ni credencials d'Instagram, i
prepara el workspace ignorat des de `config/openclaw/AGENTS.md`.

## 5. Unitats systemd

```bash
sudo install -o root -g root -m 0644 deploy/systemd/*.service \
  deploy/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable rrpp-agent-bridge-worker.service
```

El worker té hardening de `systemd`, escriu només als directoris persistents i
valida el seu heartbeat després d'arrencar. El timer executa cada minut els
healthchecks de worker, manteniment, web i ingress. Consulta'ls amb:

```bash
sudo systemctl status rrpp-agent-bridge-worker.service
sudo systemctl status rrpp-agent-bridge-healthcheck.timer
sudo journalctl -u rrpp-agent-bridge-worker.service -f
sudo journalctl -u rrpp-agent-bridge-healthcheck.service --since today
```

## 6. HTTPS amb Nginx

Substitueix `bridge.example.com` pel domini real. Primer instal·la la
configuració HTTP mínima i obtén el certificat:

```bash
export RRPP_DOMAIN=bridge.example.com
sudo sed "s/bridge.example.com/${RRPP_DOMAIN}/g" \
  deploy/nginx/bootstrap.conf.example \
  | sudo tee /etc/nginx/sites-available/rrpp-agent-bridge >/dev/null
sudo ln -s /etc/nginx/sites-available/rrpp-agent-bridge \
  /etc/nginx/sites-enabled/rrpp-agent-bridge
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
sudo snap install --classic certbot
sudo ln -sf /snap/bin/certbot /usr/local/bin/certbot
sudo certbot certonly --nginx -d "${RRPP_DOMAIN}"
```

Després instal·la la configuració final, que només proxifica el path exacte del
webhook i respon `404` a tota la resta:

```bash
sudo sed "s/bridge.example.com/${RRPP_DOMAIN}/g" \
  deploy/nginx/rrpp-agent-bridge.conf.example \
  | sudo tee /etc/nginx/sites-available/rrpp-agent-bridge >/dev/null
sudo nginx -t
sudo systemctl reload nginx
sudo certbot renew --dry-run
```

La callback pública de Meta és exactament
`https://bridge.example.com/webhooks/instagram`. No afegeixis cap `location`
cap a 8080, 18789, `/healthz`, fitxers estàtics, SQLite o backups.

## 7. Primer desplegament i desplegaments posteriors

El flux únic és pull, actualització del venv del worker, build, aturada breu,
preparació i verificació dels directoris persistents, migrate, restart i
healthcheck:

```bash
sudo bash /opt/rrpp-agent-bridge/scripts/deploy.sh
```

El script:

1. fa `git pull --ff-only` i actualitza la instal·lació editable del worker;
2. construeix una sola imatge compartida;
3. atura els processos i prepara/verifica idempotentment les ACLs de `var/`,
   `backups/` i `backup-export/` per a `rrpp` i `10001:10001`;
4. executa `rrpp-bridge migrate` com a tasca explícita, amb backup previ si cal;
5. manté aturat qualsevol worker de Compose i arrenca només web, maintenance i ingress;
6. reinicia el worker de `systemd` i activa el timer;
7. exigeix healthchecks correctes abans d'acabar.

Les aplicacions normals rebutgen una base amb migracions pendents; no apliquen
migracions implícitament. Si falla una migració o un healthcheck, no activis una
nova versió. Conserva el checkout o tag anterior per fer rollback, restaura el
codi, reconstrueix i reinicia. Una migració de dades no s'ha de revertir amb SQL
improvisat: restaura un backup verificat amb el procediment offline.

Abans d'habilitar enviaments automàtics, executa `agent-check` i `status` amb
el mateix fitxer d'entorn que usa el servei:

```bash
sudo -u rrpp -H /opt/rrpp-agent-bridge/.venv/bin/rrpp-bridge \
  --env-file /etc/rrpp-agent-bridge/rrpp.env agent-check
sudo -u rrpp -H /opt/rrpp-agent-bridge/.venv/bin/rrpp-bridge \
  --env-file /etc/rrpp-agent-bridge/rrpp.env status
```

Confirma que `agent-check` retorna
`structured: true`, que no hi ha jobs històrics pendents i que cada compte
d'Instagram usa el token correcte. Canvia primer a `canary`; només després
d'observar-lo passa a `live`.

## 8. Dashboard privat i verificació de xarxa

Obre el dashboard només amb un túnel SSH des de l'ordinador de l'operador:

```bash
ssh -N -L 8080:127.0.0.1:8080 operator@VPS_IP
```

Llavors visita `http://127.0.0.1:8080/login`. Al VPS verifica els listeners i
la superfície HTTP:

```bash
sudo ss -lntp
curl -fsS http://127.0.0.1:8080/login >/dev/null
curl -fsS http://127.0.0.1:8081/healthz
test "$(curl -sS -o /dev/null -w '%{http_code}' "https://${RRPP_DOMAIN}/healthz")" = 404
test "$(curl -sS -o /dev/null -w '%{http_code}' "https://${RRPP_DOMAIN}/login")" = 404
```

Només 22 (restringit), 80 i 443 han d'escoltar en interfícies públiques. 8080,
8081 i 18789 han de ser exclusivament loopback.

## 9. Backups i restauració

Maintenance crea backups amb l'API online de SQLite, en verifica la integritat i
reté set còpies diàries i tres mensuals. Amb `RRPP_BACKUP_AGE_RECIPIENT`, cada
backup genera també un `.age` a `backup-export/`. Si l'exportació xifrada falla,
el backup local verificat es conserva i maintenance torna a intentar exportar el
mateix fitxer abans de crear la còpia programada següent. Revisa l'alerta de
maintenance al dashboard: una còpia local pendent encara no és una còpia off-host.

Configura un job extern independent perquè copiï els `.age` a un segon
proveïdor o compte (Storage Box, object storage o servidor de backup). Usa
credencials només d'escriptura quan el destí ho permeti. Una còpia al mateix VPS
o només un snapshot Hetzner no és off-host. Comprova periòdicament des d'una
màquina segura que la identitat privada pot desxifrar una exportació i que
`rrpp-bridge backup verify` l'accepta.

Per una còpia manual, usa el mateix entorn i verifica el fitxer retornat:

```bash
sudo -u rrpp -H /opt/rrpp-agent-bridge/.venv/bin/rrpp-bridge \
  --env-file /etc/rrpp-agent-bridge/rrpp.env backup create --kind manual
sudo -u rrpp -H /opt/rrpp-agent-bridge/.venv/bin/rrpp-bridge \
  --env-file /etc/rrpp-agent-bridge/rrpp.env backup verify \
  /opt/rrpp-agent-bridge/backups/BACKUP.db
```

No copiïs secrets a l'historial del shell.

La restauració és deliberadament offline:

```bash
sudo systemctl stop rrpp-agent-bridge-worker.service
cd /opt/rrpp-agent-bridge
sudo RRPP_ENV_FILE=/etc/rrpp-agent-bridge/rrpp.env \
  docker compose --profile instagram stop web instagram maintenance
sudo -u rrpp -H .venv/bin/rrpp-bridge \
  --env-file /etc/rrpp-agent-bridge/rrpp.env restore \
  /opt/rrpp-agent-bridge/backups/BACKUP.db --confirm RESTORE
sudo bash scripts/deploy.sh
```

Si la font és `.age`, desxifra-la fora del VPS o passa `--identity` només
durant una recuperació controlada; elimina després la identitat del servidor. El
restore crea una còpia pre-restore, verifica la font i comprova la integritat de
la base restaurada abans d'acabar. A Linux, web, ingress, worker i maintenance
mantenen un lock compartit de cicle de vida; restore exigeix el lock exclusiu i
falla si algun procés continua actiu. Els backups d'una versió d'esquema anterior
encara suportada es migren automàticament amb el codi instal·lat i després es
tornen a verificar abans de reiniciar els serveis.

## 10. Operació habitual

```bash
cd /opt/rrpp-agent-bridge
sudo RRPP_ENV_FILE=/etc/rrpp-agent-bridge/rrpp.env docker compose ps
sudo RRPP_ENV_FILE=/etc/rrpp-agent-bridge/rrpp.env \
  docker compose logs --tail=100 web instagram maintenance
sudo systemctl status rrpp-agent-bridge-worker.service
sudo journalctl -u rrpp-agent-bridge-worker.service --since today
sudo systemctl list-timers rrpp-agent-bridge-healthcheck.timer
```

No imprimeixis el fitxer d'entorn en diagnòstics. Comparteix només estats,
identificadors de correlació i errors sanejats.
