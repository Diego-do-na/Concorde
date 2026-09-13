# Inventario de servicios externos — Concorde

## 1. Vultr — Cómputo / hosting del endpoint

**Estado:** Instancia creada, pendiente de arrancar (estaba en `Stopped`) y de configurar.

| Dato | Valor |
|---|---|
| Crédito | $100.00 USD confirmado, $0.03 consumido |
| Instancia | Cloud Instance (renombrar a `concorde-api`) |
| UUID | `1ef4693f-0082-49fb-ae60-8dd8ba52cead` |
| IP pública | `216.238.90.138` (IPv4 + IPv6) |
| Región | Ciudad de México — mejor latencia posible hacia Monterrey |
| Plan | `vc2-2c-4gb` — 2 vCPU, 4 GB RAM, 80 GB NVMe, $0.027/hr |
| OS | Ubuntu 24.04 LTS x64 |
| Costo estimado 40h | ~$1.10 |

**Uso:** aloja el binario Rust (`concorde-api`) bajo systemd, con Caddy como proxy inverso sirviendo HTTP (:80) y HTTPS (:443) simultáneamente. Es el endpoint que el harness de Altur va a golpear durante el judging.

**Pendientes:** arrancar instancia · verificar acceso SSH (el dropdown decía "No options" al desplegar, así que la llave puede no haber quedado inyectada) · agregar llaves de Néstor y Diego · swap de 4 GB antes de compilar · snapshot antes del congelamiento.

**Credenciales:** llaves SSH ed25519. Sin secreto en el repo.

## 2. Tiger Data — Persistencia de eventos

**Estado:** Servicio creado, esquema pendiente.

| Dato | Valor |
|---|---|
| Crédito | $1,000 USD, trial de 29 días |
| Servicio | `concorde-events` |
| Proyecto | `cc2f1ioxl1` |
| Motor | PostgreSQL + TimescaleDB |
| Compute | 0.5 CPU / 2 GiB, $0.0424/hr |
| Región | `us-west-2` (Oregon) |
| Entorno | Development, sin réplica HA, sin pooler, sin VPC |
| IP Allow List | **Ninguna** — decisión deliberada, la IP de origen cambia demasiado |
| Costo estimado 40h | ~$1.70 |

**Uso:** tabla `detection_events` como hypertable, más agregado continuo `detection_stats_1m` con `percentile_agg` para los percentiles de latencia que alimentan la vista ejecutiva. Solo métricas y veredictos — nunca audio ni transcripciones.

**Regla arquitectónica:** escritura asíncrona y no bloqueante. Si Tiger Data cae, `/detect` no se entera (§7.2).

**Credencial:** connection string completo → `TIGERDATA_URL`, solo en el `.env` del servidor.

**Pendientes:** correr `001_init.sql` desde el SQL editor para validar que `create_hypertable` y `percentile_agg` funcionan · destruir el servicio al terminar el hackathon.

## 3. Google AI Studio / Gemini API — Capa semántica

> **DECISIÓN DESCARTADA (2026-09-12).** Gemini no forma parte del sistema entregado: por privacidad del audio de judging, la capa semántica F-23 se implementó 100 % local (detector de sonda por audio + whisper.cpp + `rules.rs`, ver `docs/semantic-layer.md`). Esta sección se conserva solo como historial de la evaluación de proveedores.

**Estado:** Proyecto creado, nivel gratuito confirmado, llave pendiente de generar.

| Dato | Valor |
|---|---|
| Proyecto | `concorde` (`gen-lang-client-0616024699`) |
| Plan | Nivel gratuito, **sin facturación** |
| Cuenta | `pasb.2525@gm...` |

**Modelo elegido: Gemini 3.5 Flash Lite** — 15 RPM / 250K TPM / **500 RPD**. Alternativa con cuota independiente: 3.1 Flash Lite, mismos límites.

**Modelos descartados y por qué:**
- Todos los Flash normales (2.5, 3, 3.5, 3.6, 3.7, 3.8) → **RPD = 20**. Inservibles.
- Todos los Pro (2.5, 3.1) → cuota en 0 en nivel gratuito.
- Gemini 3.5 Transcribe → 3 RPM / 25 RPD. Inservible para transcribir 250 llamadas.
- Gemma 4 26B/31B → plan B con 30 RPM / 14.4K RPD, pero no cuenta para el premio de Gemini.

**Uso:** feature F-23, detección de invención de datos inexistentes y respuestas tipo plantilla. Prioridad SHOULD.

**Restricciones obligatorias:** timeout duro 1500 ms · caché en disco por `anon_id` · backoff exponencial ante 429 · degradación a behavioral-only sin marcar cero · **no enviar transcripciones literales del dataset**, solo derivados agregados (términos de confidencialidad de Altur).

**Nota:** los límites son por proyecto, no por llave. Si necesitan más capacidad, cada integrante debe crear su llave en un proyecto distinto.

**Credencial:** `GEMINI_API_KEY`.

## 4. ElevenLabs — Generación de clips de red team

**Estado:** Configuración de llave definida, pendiente de crear.

**Permisos a habilitar:** Text to Speech · Speech to Speech (el más valioso: convierte grabaciones humanas reales a otra voz conservando prosodia) · Voices (Read) · Models (Access) · User (Access). Opcionalmente Speech to Text si usan Scribe.

**Todo lo demás en No Access**, en particular el bloque completo de Administration.

**Configuración:** Auto-disable if leaked **encendido** · Restrict by IP **apagado** · dos llaves separadas, `-dev` con límite de créditos y `-demo` sin límite para el domingo.

**Uso:** generar voces sintéticas de un motor ausente del dataset, para la prueba de robustez (T021) y el demo en vivo. Uso invertido respecto a lo que el patrocinador espera: lo usamos para atacar nuestro propio sistema.

**Credencial:** `ELEVENLABS_API_KEY`.

**Pendiente:** verificar que el código promocional de HackMTY esté reclamado antes de generar la llave.

## 5. Whisper — Transcripción local

**No es servicio externo.** Corre en la laptop de Diego. Sustituye a Gemini Transcribe por cuota (25 RPD) y resuelve el problema de privacidad: el audio del dataset nunca sale de la máquina.

## 6. GitHub — Repositorio y coordinación

**Uso doble:** aloja el código y **es el backend de Cauce** — `tasks.yaml` versionado es el único mecanismo de coordinación, sin servidor central.

**Decisión pendiente y bloqueante para el primer deploy:** ¿el repo de Concorde es público o privado? Si es privado, la instancia de Vultr necesita su propia deploy key generada *en el servidor* y registrada en GitHub con permiso de solo lectura. Nunca copiar tu llave privada al servidor.

**El dataset no se versiona.** `.gitignore` explícito para `audio/`, `turns/`, `manifest.csv` y cualquier derivado con audio.

## 7. .Tech Domain — Premio cosmético

**Estado:** no iniciado, deliberadamente. Se ejecuta en F7 (congelamiento), nunca antes. Apunta a la consola. Esfuerzo trivial, cero impacto técnico.

## Descartados explícitamente

| Servicio | Razón |
|---|---|
| Google Cloud Platform | Exige prepago de MXN 500 y hasta 24h de acreditación. Gemini no lo necesita. |
| Google Developer Program ($10/mes) | Requiere cuenta de facturación GCP. Misma puerta, mismo bloqueo. |
| MongoDB Atlas | Redundante con Tiger Data. Duplicaría infraestructura sin retorno proporcional. |
| Snowflake | Redundante con Gemini y Tiger Data. |
| Solana | Sin relación causal con el problema. |

## Variables de entorno consolidadas

```
CONCORDE_BIND=127.0.0.1:8080
CONCORDE_MODEL_PATH=/opt/concorde/artifacts/model.onnx
CONCORDE_FEATURE_CONTRACT=fc-1
CONCORDE_THRESHOLD=0.50
CONCORDE_SEMANTIC_ENABLED=true
CONCORDE_SEMANTIC_TIMEOUT_MS=1500
CONCORDE_MAX_BODY_BYTES=33554432
CONCORDE_STRICT=0
GEMINI_API_KEY=...
ELEVENLABS_API_KEY=...
TIGERDATA_URL=postgres://...?sslmode=require
```

Archivo en `/opt/concorde/.env`, permisos `600`, dueño `concorde`, jamás en el repo.

## Análisis de dependencias críticas

De los cinco servicios externos, **solo Vultr es bloqueante**. Tiger Data, Gemini y ElevenLabs pueden caer o no estar listos y el sistema entrega igual: el almacenamiento es asíncrono, la semántica degrada por diseño, y los clips de red team son para el demo, no para el veredicto.

Esa asimetría es intencional y vale citarla en el judging: el único punto de falla externo es el hosting, y contra eso hay `Restart=always`, snapshot, e instancia de respaldo documentada.

**Gasto total proyectado: menos de $3 USD** contra $1,100 en créditos disponibles. El presupuesto nunca fue la restricción — el tiempo sí.