# RRPP Agent Bridge

`rrpp-agent-bridge` rep missatges d'Instagram, els desa abans de processar-los,
demana una decisio estructurada a OpenClaw i deixa que el bridge decideixi si pot
respondre automaticament o si cal una persona. Cada pas queda visible i auditat.

```text
Instagram webhook
-> event i conversa persistents
-> job durable
-> worker
-> OpenClaw rrpp
-> politica del bridge
-> draft, escalat o enviament oficial
-> dashboard privat
```

OpenClaw no te el token d'Instagram i no pot enviar res directament. El worker es
l'unic component que pot usar l'API oficial de Meta, despres de comprovar politica,
mode, pausa de conversa, idempotencia i estat del missatge.

## Requisits

- Windows amb Python 3.12 o superior.
- Compte professional d'Instagram vinculat a una Facebook Page.
- Meta Developer App amb webhooks de missatgeria i els permisos aplicables.
- OpenClaw local amb el Gateway a `127.0.0.1:18789`.
- Un tunel HTTPS que exposi nomes el port del webhook (`8081`).

Docker no es necessari per a la demo local.

## Primera instal·lacio

Des de PowerShell, a l'arrel del repositori:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
Copy-Item .env.example .env
.\.venv\Scripts\rrpp-bridge.exe migrate
```

No sobreescriguis un `.env` existent. `.env`, tokens i credencials estan ignorats
per Git i no s'han de publicar ni enganxar en documentacio.
Abans d'arrencar el dashboard, assigna a `.env` una contrasenya i un secret de
sessió aleatoris i diferents; la plantilla els deixa buits perquè la configuració
incompleta falli de manera segura.

## Configuracio

Variables principals de l'Instagram inbound i outbound:

```text
RRPP_INSTAGRAM_ENABLED=true
RRPP_INSTAGRAM_SEND_ENABLED=true
RRPP_INSTAGRAM_PORT=8081
RRPP_INSTAGRAM_GRAPH_BASE_URL=https://graph.instagram.com
RRPP_INSTAGRAM_GRAPH_API_VERSION=v24.0
RRPP_INSTAGRAM_SEND_TIMEOUT_SECONDS=15
RRPP_RESPONSE_DEBOUNCE_SECONDS=3
INSTAGRAM_VERIFY_TOKEN=valor-privat-del-webhook
INSTAGRAM_APP_SECRET=secret-de-la-meta-app
INSTAGRAM_PAGE_ACCESS_TOKEN=token-del-compte-professional
INSTAGRAM_BUSINESS_ACCOUNT_ID=id-del-compte-per-a-la-Send-API
INSTAGRAM_WEBHOOK_ACCOUNT_ID=id-receptor-observat-al-webhook
```

La configuracio anterior continua sent valida per a un sol compte. Per rebre i
respondre des de mes d'un compte de la mateixa Meta App, substitueix les tres
variables singulars del compte per un registre explicit:

```text
INSTAGRAM_ACCOUNTS_JSON=[{"alias":"principal","webhook_account_id":"id-receptor-1","business_account_id":"id-send-api-1"},{"alias":"secundari","webhook_account_id":"id-receptor-2","business_account_id":"id-send-api-2"}]
INSTAGRAM_ACCOUNT_PRINCIPAL_ACCESS_TOKEN=token-del-compte-principal
INSTAGRAM_ACCOUNT_SECUNDARI_ACCESS_TOKEN=token-del-compte-secundari
```

Els alias han de comencar per una lletra minuscula i nomes poden contenir
minuscules, numeros i `_`. No combinis `INSTAGRAM_ACCOUNTS_JSON` amb
`INSTAGRAM_PAGE_ACCESS_TOKEN`, `INSTAGRAM_BUSINESS_ACCOUNT_ID` o
`INSTAGRAM_WEBHOOK_ACCOUNT_ID`. El verify token i l'App Secret continuen sent
compartits: aquesta configuracio admet diversos comptes de la mateixa Meta App;
una segona App necessita un ingress i una revisio de seguretat separats.

Variables d'OpenClaw:

```text
OPENCLAW_ENABLED=true
OPENCLAW_BASE_URL=http://127.0.0.1:18789
OPENCLAW_AGENT_ID=rrpp
OPENCLAW_TIMEOUT_SECONDS=60
OPENCLAW_GATEWAY_TOKEN=token-local-del-gateway
```

El token de verificacio el tries tu i ha de coincidir amb Meta. L'App Secret, el
token d'acces i l'ID del compte provenen de la Meta Developer App. El Gateway token
ha de coincidir amb la configuracio local d'OpenClaw.

Amb Instagram Login, el token necessita com a minim
`instagram_business_basic` i `instagram_business_manage_messages`; `/me` ha de
retornar el compte professional utilitzat per la Send API. Meta pot entregar un
identificador de receptor diferent a `entry.id`; aquest valor s'ha de configurar
separadament com a `INSTAGRAM_WEBHOOK_ACCOUNT_ID` o `webhook_account_id` i mai
s'ha d'inferir del text. Cada missatge entrant s'accepta nomes si el receptor es
a la llista exacta configurada, i cada resposta usa exclusivament el token i
l'ID Send API associats a aquell receptor.

El repositori nomes versiona la plantilla segura a `config/openclaw/AGENTS.md`.
El workspace real d'OpenClaw viu a `var/openclaw-workspace/`, queda fora de Git i pot
contenir estat local. Per preparar-lo i apuntar-hi l'agent:

```powershell
New-Item -ItemType Directory -Force var\openclaw-workspace | Out-Null
Copy-Item config\openclaw\AGENTS.md var\openclaw-workspace\AGENTS.md -Force
cmd /c openclaw config set agents.list[1].workspace C:\ruta\al\projecte\var\openclaw-workspace
```

L'agent `rrpp` ha de tenir `tools.deny=["*"]`, cap binding de canal i eines elevades
desactivades.

## Comprovar OpenClaw

PowerShell pot bloquejar el launcher `.ps1` de npm; utilitza el wrapper `.cmd`:

```powershell
cmd /c openclaw gateway status
.\.venv\Scripts\rrpp-bridge.exe agent-check
```

La segona ordre no envia cap missatge. El resultat correcte inclou:

```json
{"action":"reply","provider":"openclaw","reason_code":"greeting","status":"healthy","structured":true}
```

No activis la resposta automàtica si `status` no és `healthy` o `structured` no
és `true`. En producció passa sempre el fitxer protegit amb `--env-file`; una
shell directa no hereta l'`EnvironmentFile` del worker de systemd.

## Arrencar la demo

Executa cada servei en una terminal separada.

Terminal 1, dashboard privat:

```powershell
.\.venv\Scripts\rrpp-bridge.exe web
```

Terminal 2, webhook d'Instagram:

```powershell
.\.venv\Scripts\rrpp-bridge.exe instagram-webhook
```

Terminal 3, worker i enviaments:

```powershell
.\.venv\Scripts\rrpp-bridge.exe worker
```

Terminal 4, tunel public nomes cap al webhook:

```powershell
cloudflared tunnel --url http://127.0.0.1:8081
```

A Meta, la callback exacta es:

```text
https://EL-TEU-HOST/webhooks/instagram
```

Subscriu el camp de missatgeria corresponent als DMs. No exposis ni tunelitzis el
port `8080`; el dashboard continua a `http://127.0.0.1:8080/login`.

Comprova que no hi ha cua antiga i activa el mode automatic:

```powershell
.\.venv\Scripts\rrpp-bridge.exe status
.\.venv\Scripts\rrpp-bridge.exe set-mode live
```

La UI mostra nomes els dos comportaments operatius:

- `Nomes lectura`: genera una proposta i no envia.
- `Automatic`: envia nomes quan la politica determinista ho permet.

Per a la primera prova, envia `Hola` per DM des d'un usuari que ja hagi iniciat la
conversa amb el compte professional. El dashboard ha de mostrar el missatge inbound,
la decisio d'OpenClaw, el job, l'enviament i el missatge outbound.

## Quan respon i quan escala

Pot respondre automaticament a salutacions, agraiments, comiats, aclariments segurs
i respostes basades en elements verificats del cataleg. Les respostes comercials han
de referenciar els elements exactes que OpenClaw ha rebut.

Sempre escala reserves, guest list, VIP o taules, pagaments, devolucions, queixes,
seguretat, dades personals, informacio desconeguda o respostes que no compleixen
l'esquema. Un timeout o resultat ambigu d'Instagram pausa el bot per a aquella
conversa i evita un reintent cec.

Des de la conversa del dashboard una persona pot editar o escriure una resposta,
enviar-la per la mateixa cua auditada, pausar o reprendre el bot i resoldre o reobrir
la conversa.

## Cataleg comercial

`Discoteques` administra informacio verificada, esdeveniments i ofertes. Una conversa
pertany al canal, compte receptor i usuari extern; no pertany obligatoriament a una
discoteca. OpenClaw pot comparar diverses opcions sense inferir una discoteca a partir
del text del client.

## Operacions

```powershell
.\.venv\Scripts\rrpp-bridge.exe status
.\.venv\Scripts\rrpp-bridge.exe worker --once
.\.venv\Scripts\rrpp-bridge.exe maintenance --once
.\.venv\Scripts\rrpp-bridge.exe backup create --kind manual
.\.venv\Scripts\rrpp-bridge.exe backup verify backups\BACKUP.db
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Per executar localment les mateixes portes de qualitat que CI:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[deployment,quality]"
.\.venv\Scripts\ruff.exe check rrpp_bridge tests
.\.venv\Scripts\python.exe -m mypy rrpp_bridge
.\.venv\Scripts\python.exe -m bandit -q -r rrpp_bridge -s B404,B603
.\.venv\Scripts\python.exe -m build
```

`scripts/run-local.ps1` inicia dashboard, worker i manteniment. El webhook i el tunel
es mantenen separats per no exposar accidentalment el dashboard.

Per a desplegament i backups, consulta [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md). Les
decisions, invariants i errors reutilitzables son a
[docs/agent-guide/](docs/agent-guide/README.md).
