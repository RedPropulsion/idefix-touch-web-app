# LED e segnale LoRa su Idefix

Il pannello ha due schede in alto: **LED** e **Segnale LoRa**. Su un touchscreen
800×480 i controlli principali e i due grafici entrano senza scorrimento;
su telefono i grafici si dispongono uno sotto l'altro. Le risorse sono locali:
non servono Internet o librerie grafiche scaricate dal browser.

## Che cosa misura

ObelICS continua a inviare quattro byte `PING` alla WL55 ogni cinque secondi;
la WL55 continua a rispondere con quattro byte `PONG`. Il suo firmware non cambia.
Idefix riceve le osservazioni di ObelICS via MAVLink/Ethernet:

- RSSI, in dBm: potenza del **PONG ricevuto da ObelICS**.
- SNR, in dB: rapporto segnale/rumore dello stesso PONG.
- Risposte/PING: PONG validi diviso trasmissioni PING completate, cumulativi
  dall'avvio di ObelICS; non sono i contatori della sola prova sul grafico.
- Timeout: attese radio scadute. Errori: problemi di configurazione, TX o RX.
- RTT: dal principio dell'invio del PING alla ricezione del PONG, incluso il
  ritardo intenzionale di 200 ms della WL55. Non è una misura della sola propagazione.

Un pacchetto diverso da PONG aumenta RX ma non le risposte valide. Dopo un timeout
le schede mantengono gli ultimi valori validi con la loro età, mentre il grafico
lascia un'interruzione e una croce in basso. I campioni mancanti via Ethernet
interrompono anch'essi la linea; non diventano timeout radio. Dopo 12 secondi
senza una fotografia completa delle statistiche compare “Telemetria interrotta”.
Non ricaviamo distanza o una percentuale inventata di qualità da RSSI/SNR.

Il protocollo radio conserva il limite attuale: una coppia di nodi, PING/PONG
senza sequenza nel payload. La numerazione descritta sotto riguarda esclusivamente
la telemetria Ethernet, non rende il protocollo radio un protocollo numerato.

## Esperimento

**Avvia prova** comincia una nuova raccolta condivisa da touchscreen e telefono.
**Ferma prova** congela il grafico; il ping-pong e le metriche correnti continuano.
**Nuova prova** sostituisce la raccolta precedente. Il grafico mostra gli ultimi
cinque minuti della raccolta; si conservano in RAM al massimo 6000 campioni,
oltre otto ore alla cadenza attuale. I dati non sono salvati su disco e si perdono
al riavvio del servizio. L'asse temporale usa tempo monotono locale, quindi non
dipende da NTP; l'orario dell'ultimo ACK usa Europe/Rome.

La WL55 può essere alimentata dalla USB di un altro PC portatile. Una volta
programmata parte da sola: il secondo PC non deve essere collegato alla rete
Idefix e non deve avere Zephyr. Evitare che vada in sospensione durante la prova.

## Installazione su un Idefix già configurato

Aggiornare prima il firmware di ObelICS con la modifica corrispondente in
Panoramix (`demo_mavlink_publish_lora` e raccolta delle statistiche). Non occorre
riprogrammare la WL55. Il nuovo pannello continua a comandare i LED con il vecchio
firmware, ma la pagina segnale rimane in attesa finché manca la nuova telemetria.

I servizi e gli indirizzi restano quelli esistenti. Prima di sostituire i file,
confrontare la copia installata con la repository se sono state fatte modifiche
manuali sul Raspberry. Su Idefix, salvare una copia della cartella applicativa:

```bash
cp -a /home/pi/idefix-touch /home/pi/idefix-touch.backup-$(date +%Y%m%d-%H%M%S)
sudo systemctl stop idefix-web.service
```

Dal PC, nella radice di questa repository, copiare i file applicativi:

```bash
scp idefix-touch/idefix_web.py idefix-touch/idefix_link.py idefix-touch/idefix_mavlink.py pi@10.42.0.1:/home/pi/idefix-touch/
scp idefix-touch/static/index.html idefix-touch/static/panel.css idefix-touch/static/panel.js pi@10.42.0.1:/home/pi/idefix-touch/static/
```

Su Idefix:

```bash
sudo systemctl restart idefix-web.service idefix-kiosk.service
systemctl --no-pager --full status idefix-web.service
journalctl -u idefix-web.service --no-pager -n 50
```

Il vecchio ambiente `.venv` con pymavlink è sufficiente. Non serve reinstallare
i servizi se il loro percorso e l'IP di ObelICS sono invariati. La porta UDP
14551 ora resta occupata dal servizio: fermarlo prima di usare separatamente
la CLI `idefix_mavlink.py` sulla stessa porta. Per tornare alla versione precedente,
fermare il servizio, ripristinare i file dal backup e riavviare i due servizi.

## Anteprima senza schede

Dalla cartella `idefix-touch`, usando l'ambiente Python disponibile:

```bash
python3 idefix_web.py --host 127.0.0.1 --demo --web-port 8098
```

Aprire <http://127.0.0.1:8098>. La pagina indica **DATI SIMULATI**; il selettore
consente Vicino, Più lontano, Dietro un ostacolo e WL55 spenta. Gli aggiornamenti
arrivano ogni cinque secondi. La modalità demo non apre socket MAVLink né invia
comandi hardware. Non aggiungere `--demo` al servizio della dimostrazione reale.

## Architettura

`idefix_link.py` mantiene una sola connessione UDP e un thread ricevitore.
I comandi LED sono serializzati e attendono un evento ACK: non leggono direttamente
il socket. Il ricevitore indirizza ogni messaggio all'ACK in attesa oppure alla
raccolta LoRa, verificando system/component del mittente e destinazione dell'ACK.

Il protocollo LED rimane COMMAND_LONG/COMMAND_ACK con ID 60000–60003. Non vengono
aggiunti comandi al LED WL55 né un nuovo dialetto. La telemetria usa
`NAMED_VALUE_INT` del dialetto `common`; tutte le voci di uno snapshot hanno lo
stesso `time_boot_ms` di ObelICS. Solo snapshot completi sono accettati, anche
se i datagrammi arrivano fuori ordine. Un gruppo incompleto scade dopo tre secondi.

| Nome | Significato |
|---|---|
| LR_BOOT | Identificativo casuale a 31 bit generato all'avvio del trasporto |
| LR_SEQ | Numero del ciclo di osservazione |
| LR_TX / LR_RX | Invii completati / pacchetti radio ricevuti |
| LR_OK / LR_TO / LR_ERR | PONG validi / timeout / errori radio |
| LR_RESULT | 1 PONG, 2 timeout, 3 payload inatteso, 4 errore, 5 radio indisponibile |
| LR_RSSI / LR_SNR | Ultime metriche di un PONG valido, significative solo se LR_AGE ≥ 0 |
| LR_RTT | Tempo dell'ultimo ciclo riuscito in ms; −1 negli altri casi |
| LR_AGE | Età dell'ultimo PONG in ms; −1 se mai ricevuto |
| LR_END | Versione dello schema: 1 |

Sequenza e identificativo di avvio impediscono di duplicare campioni o mescolare
statistiche di riavvii diversi. Un mutex sul firmware serializza costruzione e
invio dei messaggi MAVLink, sia telemetria sia ACK.

API HTTP:

- `POST /api/led` con `{"mode":"spin"}`: interfaccia esistente, quattro modalità.
- `GET /api/lora/state?after=<id>`: stato corrente e nuovi punti della raccolta.
- `POST /api/lora/experiment` con `{"action":"start"}` o `{"action":"stop"}`.
- `POST /api/demo/scenario` disponibile esclusivamente con `--demo`.

Tutte le POST richiedono il token della pagina. Il server fornisce un identificativo
di istanza per permettere ai browser di recuperare correttamente dopo un riavvio.
La pagina esegue polling ogni secondo, senza sovrapporre richieste.

## Verifica

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -v
```

I test comprendono un collegamento UDP locale con veri frame MAVLink. Per una
verifica completa sulle schede: provare i quattro effetti LED mentre arrivano
campioni LoRa, avviare/fermare una raccolta, spegnere/riaccendere la WL55,
riavviare ObelICS, scollegare/ricollegare Ethernet e aprire insieme touchscreen
e telefono. Confermare che valori vecchi non diventano nuovi campioni. Segue
una prova continuativa di durata rappresentativa delle 6–8 ore della demo.
