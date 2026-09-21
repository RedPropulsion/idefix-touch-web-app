# Idefix Touch

Pannello touchscreen per i LED di ObelICS. Configurazione: Raspberry Pi con
Trixie, utente `pi`, applicazione in `/home/pi/idefix-touch`.
Per il funzionamento del codice vedere [SPIEGAZIONE_CODICE.md](SPIEGAZIONE_CODICE.md).

## Avvio su un Idefix già configurato

Su Idefix, anche via SSH:

```bash
sudo systemctl restart idefix-web.service idefix-kiosk.service
```

La pagina compare sul touchscreen. Toccare un effetto e verificare **ACK ricevuto**.
Se ObelICS è simulato, avviare prima `listen` sul PC come indicato sotto.

## Prima installazione

Dal PC, nella directory che contiene `idefix-touch`, copiare la cartella:

```bash
scp -r idefix-touch pi@10.42.0.1:~/
```

Per usare `10.42.0.1`, il PC deve essere collegato all’hotspot di Idefix.
Se si usa SSH via Ethernet, sostituire l’indirizzo con `192.168.10.1`.

Su Idefix, con Internet disponibile per scaricare i pacchetti:

```bash
sudo apt update
sudo apt install cage chromium chromium-sandbox dbus-user-session libpam-systemd curl libinput-tools kbd python3-venv
cd ~/idefix-touch
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Chiudere eventuali istanze manuali di `idefix_web.py` e console `interactive`
del tool, che occuperebbero le porte 8080 e 14551.

Installare e avviare i servizi. **Sostituire `192.168.10.2` se il destinatario
MAVLink (ObelICS oppure il PC simulatore) ha un altro IP.**

```bash
sudo bash install-services.sh 192.168.10.2
sudo systemctl restart idefix-web.service idefix-kiosk.service
```

Dopo una prova riuscita, abilitare l’avvio automatico:

```bash
sudo systemctl enable idefix-web.service idefix-kiosk.service
```

## Prova con ObelICS simulato sul PC

Sul PC, nella cartella del tool e con il suo ambiente Python attivo:

```bash
python3 idefix_mavlink.py listen
```

Lasciare il processo aperto. Il pannello deve puntare all’IP Ethernet del PC
(`192.168.10.2` nella configurazione provata). SSH può usare contemporaneamente
il Wi-Fi dell’hotspot: mantenere il cavo Ethernet per MAVLink.

## Aggiornamenti e arresto

Dopo aver ricopiato i file, riavviare entrambi i servizi. Per cambiare l’IP del
destinatario, rieseguire anche `install-services.sh` con il nuovo indirizzo.

Per fermare l’applicazione e tornare alla console:

```bash
sudo systemctl stop idefix-kiosk.service idefix-web.service
sudo chvt 1
```

Per disabilitare anche l’avvio al boot:

```bash
sudo systemctl disable idefix-kiosk.service idefix-web.service
```

## Controlli in caso di problemi

Su Idefix:

```bash
systemctl --no-pager --full status idefix-web.service idefix-kiosk.service
curl --max-time 5 --fail http://127.0.0.1:8080/api/info
journalctl -b -u idefix-web.service -u idefix-kiosk.service --no-pager -n 80
```

Se manca l’ACK, verificare IP del destinatario, Ethernet e processo `listen`.
Per problemi di display o touch eseguire `sh /home/pi/idefix-touch/check-display.sh`.

Per aprire la pagina anche dal PC, lasciare aperto questo tunnel SSH:

```bash
ssh -L 8080:127.0.0.1:8080 pi@10.42.0.1
```

Aprire poi [http://127.0.0.1:8080](http://127.0.0.1:8080) nel browser del PC.

## Ora corretta per la demo offline

Dopo l’accensione verificare `date` su Idefix. Se l’ora è errata, eseguire
**dal PC con ora corretta, fuori dalla sessione SSH**:

```bash
ssh -t pi@10.42.0.1 "sudo timedatectl set-ntp false"
ssh -t pi@10.42.0.1 "sudo date -u -s '@$(date +%s)'"
ssh -t pi@10.42.0.1 "sudo timedatectl set-ntp true"
```

Inviare un nuovo comando per aggiornare l’ora dell’ultimo ACK nella pagina.
