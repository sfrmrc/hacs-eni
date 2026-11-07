# Eni Plenitude per Home Assistant

Integrazione custom (HACS) che legge i **consumi orari della fornitura luce** dall'Area Personale
[Eni Plenitude](https://eniplenitude.com/my-eni) e li importa come statistiche esterne,
pronte per l'**Energy Dashboard**.

> Integrazione non ufficiale, basata sul reverse engineering del portale: può smettere di funzionare
> se Plenitude cambia il sito.

## Cosa fa

- Login con email e password dell'Area Personale (gli account Google/Apple non sono supportati).
- Rileva da solo POD e identificativi del contratto. Se l'account ha più forniture luce, ti chiede quale configurare.
- Importa i **consumi orari** in kWh e il **costo** (kWh × prezzo medio) come statistiche esterne:
  - `eniplenitude:<pod>_energy` (kWh)
  - `eniplenitude:<pod>_cost` (EUR)
- Al primo avvio carica gli ultimi **60 giorni** (il portale conserva circa 4 mesi di dati orari).
  Gli aggiornamenti successivi aggiungono solo le ore nuove, senza contare due volte.
- Aggiorna ogni 12 ore. Il portale pubblica i dati con 2-3 giorni di ritardo.
- Sensori:

| Sensore | Unità | Note |
|---|---|---|
| Consumo mensile | kWh | totale del mese dell'ultimo dato disponibile, dal portale |
| Costo stimato mensile | EUR | consumo mensile × prezzo medio |
| Ultimo dato disponibile | data | ultimo giorno con dati completi |
| Prezzo per kWh | EUR/kWh | diagnostico, valore impostato da te |

## Installazione

### HACS
1. HACS → menu ⋮ → *Repository personalizzati* → aggiungi l'URL di questo repository, categoria *Integrazione*.
2. Installa **Eni Plenitude** e riavvia Home Assistant.

### Manuale
Copia `custom_components/eniplenitude` dentro `<config>/custom_components/` e riavvia.

Serve Home Assistant **2025.11** o successivo.

## Configurazione

1. *Impostazioni → Dispositivi e servizi → Aggiungi integrazione → Eni Plenitude*.
2. Inserisci email, password e prezzo medio €/kWh (tutto compreso: materia energia, trasporto, oneri, imposte).
   Puoi ricavarlo da una bolletta: totale ÷ kWh fatturati.
3. **Energy Dashboard**: *Impostazioni → Plance → Energia → Aggiungi consumo* e scegli
   la statistica **Plenitude &lt;POD&gt; energia**. Per il costo seleziona *Usa un'entità che tiene traccia dei costi totali*
   e scegli **Plenitude &lt;POD&gt; costo**.

   Non usare il sensore *Consumo mensile* nell'Energy Dashboard: è un riepilogo e non ha la granularità oraria.

Per cambiare il prezzo vai su *Configura* nell'integrazione. Il nuovo prezzo vale solo per le ore importate da quel momento in poi.

Se la password cambia o scade, Home Assistant ti chiede di autenticarti di nuovo.

## Come funziona (per sviluppatori)

1. Login SAP Gigya: `accounts.webSdkBootstrap` (imposta i cookie gmid, senza `getJWT` risponde 403005), `accounts.login` → `login_token`, poi `accounts.getJWT` → `id_token`
   (apiKey `4_QsDZw2Yu7M9yCRldyBrBhg`, datacenter eu1).
2. `GET /serviceDAp/api/c360/getJwe` con header `JWT: <id_token>`: il portale imposta i cookie di sessione.
   Tutte le chiamate successive usano solo i cookie, più i parametri `logHash` (casuale) e `channel=PORTAL`.
3. Scoperta delle forniture:
   `api/c360/init` → `contiServices/conti/{conto}` → `contiServices/conti/{conto}/lista-forniture` (tipo `POWER`)
   → `POST profilo-energetico-services/{conto}/users/{cliente}/supplies` `{pod, pdr}`, che restituisce
   `clientCode`, `contractId`, `houseId` e `serviceLastData`.
4. Consumi: `POST profilo-energetico-services/{conto}/analysis/consumptions`
   `{clientCode, contractId, houseId, startTime, endTime, type}`.
   Con `type: "0"` restituisce un giorno con 24 valori orari; con `type: "2"` un mese con valori giornalieri.
   Si usa la serie `Energia attiva`.

Per provare login e chiamate fuori da Home Assistant:

```bash
uv run --with aiohttp python scripts/check_api.py tua@email.it
```

Test:

```bash
uv run --python 3.14 --with pytest-homeassistant-custom-component python -m pytest
```

## Limitazioni note

- **Captcha**: se Gigya decide di chiedere un captcha (per esempio dopo molti tentativi falliti), il login da Home Assistant non funziona.
  Accedi una volta da browser e riprova.
- **Giorni senza dati**: il portale non ha dati orari per alcuni giorni, per esempio quelli del cambio dell'ora legale.
  Quei giorni vengono saltati e non vengono recuperati in seguito.
- **Prezzo**: è un prezzo medio fisso impostato da te. Le fasce F1/F2/F3 e le offerte indicizzate non sono ancora gestite.
  Il punto da estendere è `PlenitudeCoordinator.price` / `_async_import_hourly`.
  Il portale espone anche i consumi mensili per fascia (`api/consumptionData`) e il nome dell'offerta (`prodotto`, `tipologiaOfferta`).
- Solo **luce**. Il gas usa le stesse API (`pdr`, `clientType: GAS`) ed è una possibile evoluzione.
