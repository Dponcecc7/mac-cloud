# Desplegar mac_cloud en el servidor de TI

> Reemplaza al setup anterior (Render + Aiven). TI va a dar un servidor propio y una base PostgreSQL -- este documento es la guía para ese traspaso. Quedan dos decisiones abiertas marcadas más abajo que hay que resolver con TI antes del último paso.

## 0. Variables de entorno que necesita el proyecto
Ver [.env.example](.env.example) -- tiene la lista completa (Postgres, Athena/AWS, Microsoft Graph/SharePoint, GitHub) con una nota de qué usa cada una.

## 1. Preparar el servidor
1. Instalar Python 3.12.
2. Clonar el repo (es público, no hace falta token):
   ```
   git clone https://github.com/Dponcecc7/mac-cloud.git
   cd mac-cloud
   ```
3. Crear un venv **dedicado a este proyecto** (nunca instalar nada global):
   ```
   python -m venv .venv
   .venv\Scripts\activate          # Windows
   # source .venv/bin/activate     # Linux
   pip install -r requirements.txt
   ```

## 2. Base de datos PostgreSQL
TI da host, puerto, usuario, contraseña y nombre de la base. Con eso arma `DATABASE_URL`:
```
postgresql+psycopg://USUARIO:PASSWORD@HOST:PUERTO/NOMBRE_BD?sslmode=require
```

Crear el esquema (tablas + la vista `vacantes`) -- dos formas, cualquiera de las dos sirve:
- **Desde el SQL ya versionado**: correr [docs/schema_mac.sql](docs/schema_mac.sql) directo contra la base nueva (`psql -f docs/schema_mac.sql ...` o pegarlo en un cliente como pgAdmin/DBeaver).
- **Desde el código** (recrea lo mismo, es la fuente de verdad real): `python seed_admin.py` (crea `usuarios` y pide el primer admin) + `python dimension_models.py` (crea el resto de las tablas y la vista `vacantes`).

### Migrar los datos actuales (si corresponde)
Si la base nueva arranca vacía, no hace falta nada más acá. Si en cambio se quiere traer lo que ya hay hoy en Render (personas, histórico de visitas, patrón recurrente, etc.), usar [_backups_db/migrar_a_servidor_ti.py](_backups_db/migrar_a_servidor_ti.py) -- mismo script (adaptado) que ya se usó para la migración Aiven → Render de septiembre. Pasos:
1. Abrir el archivo y pegar la cadena de conexión real del Postgres de TI en `TARGET_URL_CRUDA`.
2. Correr con `DATABASE_URL` apuntando a Render (la base de **origen**):
   ```
   .venv\Scripts\python.exe _backups_db\migrar_a_servidor_ti.py
   ```
3. El script recrea el esquema en destino, copia todas las tablas en el orden correcto (respetando foreign keys), reacomoda las secuencias de `id`, y al final compara conteos origen vs. destino fila por fila -- si algo no coincide, lo avisa en vez de fallar en silencio.
4. **Recomendado**: probarlo primero contra una base de TI de prueba/vacía antes de correrlo contra la definitiva -- el paso 0 del script borra lo que haya en destino, no es no-destructivo.
5. Este script vive en `_backups_db/` (gitignorado) porque durante su uso tiene pegada una cadena de conexión real -- nunca se sube al repo.

## 3. Variables de entorno en el servidor
Setear todas las de `.env.example` que apliquen (ver decisión del pipeline, paso 5) -- como variables de entorno del sistema o en un archivo `.env` junto al código (nunca subirlo a git, ya está en `.gitignore`).

**No te olvides de `SECRET_KEY`** -- hoy la app corre con un valor de respaldo público porque nunca se seteó en Render; en este traspaso es el momento de cerrarlo con un valor real y random.

## 4. Correr la app como servicio
```
gunicorn wsgi:application --bind 0.0.0.0:PUERTO
```
Nunca el servidor de desarrollo de Flask (`python app.py`) en producción. Configurarlo para que arranque solo y se reinicie si se cae -- Programador de tareas + NSSM si es Windows, `systemd` si es Linux (el método exacto depende de qué les dé TI).

Si el acceso va a ser también desde fuera de la red interna, falta un reverse proxy con HTTPS (nginx/IIS) adelante -- y si no hay HTTPS, `SESSION_COOKIE_SECURE` tiene que pasar a `false`, si no el login no guarda la cookie de sesión.

## 5. El pipeline programado -- DECISIÓN PENDIENTE CON TI
Hoy el trabajo pesado (traer visitas de Athena, exportar dimensiones, reporte 9am) corre en **GitHub Actions**, disparado por un servicio externo (cron-job.org) que le pega a `/cron/pipeline` y `/cron/exportar` de la app cada 5 min. Dos caminos:

- **(A) Seguir usando GitHub Actions** -- el servidor de TI solo hostea la app web. Las credenciales de Athena/Graph siguen viviendo en los Secrets de GitHub (ya están ahí), no hace falta ponerlas en el servidor nuevo. Pero esto requiere que cron-job.org pueda seguir pegándole a `/cron/pipeline`/`/cron/exportar`, o sea que esos dos endpoints sean alcanzables desde internet -- si el servidor de TI no acepta conexiones entrantes (problema ya visto con Acronis en otra máquina), este camino no funciona tal cual.
- **(B) Mover todo al servidor de TI** -- Programador de Tareas corriendo `python pipeline/pipeline_completo.py`, `python exportar_dimensiones.py`, `python actualizar_essity_headcount.py`, `python pipeline/ciclo_reporte_9am.py` directo, sin pasar por HTTP ni por GitHub Actions. Ahí sí hace falta tener las credenciales de Athena/AWS y Graph/SharePoint en el servidor nuevo, y los workflows de `.github/workflows/` (salvo `tests.yml`, que es CI y no depende de esto) quedan sin uso.

## 6. Probar
`http://servidor:puerto/health` debe responder `{"status": "ok", "db": true}`. Después `/login` con un usuario admin (el que creó `seed_admin.py`, o uno ya migrado si se corrió el paso de migración de datos).

## Cosas a tener en cuenta
- Si se elige el camino (A) del paso 5, cada `git push` a `main` sigue redeployando sola la app **en Render** -- para que redespliegue en el servidor de TI hace falta armar ese mecanismo de nuevo ahí (git pull + reiniciar el servicio, manual o con un webhook).
- El repo es público en GitHub -- TI puede verlo con solo el link, no hace falta invitarlos como colaboradores.
