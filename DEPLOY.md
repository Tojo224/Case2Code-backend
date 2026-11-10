# Despliegue en AWS EC2 (una sola VM, Docker Compose)

```
Internet ──> nginx (frontend, :80/:443) ──> FastAPI (backend, :8000, interno) ──> PostgreSQL (interno)
```

Solo nginx se publica. El backend y PostgreSQL solo son alcanzables dentro de la red de Compose.

## 1. La VM

| Concepto | Valor |
|---|---|
| AMI | Ubuntu Server 24.04 LTS |
| Tipo | `t3.small` (2 GB) como mínimo, con swap; recomendado `t3.medium` (4 GB) |
| Disco | 20 GB gp3 |
| IP | Asociá una **Elastic IP** (si no, la IP cambia al reiniciar) |

**Security Group** (entrada):

| Puerto | Origen | Para qué |
|---|---|---|
| 22 | solo tu IP | SSH |
| 80 | 0.0.0.0/0 | web y desafío de Let's Encrypt |
| 443 | 0.0.0.0/0 | HTTPS (si usás dominio) |

No abras 5432 ni 8000.

Swap (solo si la VM tiene 2 GB o menos; el build del frontend usa mucha memoria):

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

## 2. Docker

```bash
sudo apt-get update && sudo apt-get install -y ca-certificates curl git
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER   # cerrá sesión y volvé a entrar
```

## 3. Código y configuración

Los dos repos deben estar **uno al lado del otro** (el compose construye `../Case2Code-frontend`):

```bash
git clone https://github.com/Tojo224/Case2Code-backend.git
git clone https://github.com/Tojo224/Case2Code-frontend.git
cd Case2Code-backend
cp .env.production.example .env
```

Completá `.env`:

```bash
echo "JWT_SECRET=$(openssl rand -hex 32)"
echo "POSTGRES_PASSWORD=$(openssl rand -hex 24)"   # solo hex: va dentro de una URL
```

- `JWT_SECRET` y `POSTGRES_PASSWORD` son obligatorios. Sin ellos Compose no arranca.
- En producción el backend **se niega a iniciar** con el secreto por defecto, uno menor de 32 caracteres o una base SQLite.
- `GEMINI_API_KEY` es opcional. Sin Gemini ni Ollama, el asistente usa el motor por reglas.

## 4. Levantar

```bash
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps     # backend y frontend deben quedar "healthy"
curl http://localhost/api/health
```

Abrí `http://<IP-elastica>/`. El primer build tarda varios minutos.

## 5. HTTPS (necesita un dominio propio)

Let's Encrypt **no** emite certificados para los nombres `*.amazonaws.com`. Hace falta un dominio con un registro A hacia la Elastic IP.

1. En `.env`: `TLS_DOMAIN=app.tudominio.com`.
2. `docker compose -f docker-compose.prod.yml up -d` (nginx arranca con un certificado autofirmado temporal).
3. Emití el certificado real:

   ```bash
   docker compose -f docker-compose.prod.yml run --rm --entrypoint certbot certbot \
     certonly --webroot -w /var/www/certbot -d app.tudominio.com \
     --email vos@tudominio.com --agree-tos --no-eff-email
   ```

4. Reiniciá nginx una vez para que use el certificado real, y activá la renovación automática:

   ```bash
   docker compose -f docker-compose.prod.yml restart frontend
   docker compose -f docker-compose.prod.yml --profile tls up -d certbot
   ```

Con `TLS_DOMAIN` seteado, el puerto 80 solo redirige a HTTPS. El WebSocket pasa a `wss://` solo, porque el frontend usa el mismo origen.

## 6. Actualizar

```bash
cd Case2Code-backend  && git pull
cd ../Case2Code-frontend && git pull
cd ../Case2Code-backend
docker compose -f docker-compose.prod.yml up -d --build
```

Las tablas y columnas nuevas se crean solas al arrancar el backend. Los datos viven en el volumen `postgres_data` y sobreviven a los redeploys.

## 7. Backup y restauración

```bash
docker compose -f docker-compose.prod.yml exec -T postgres \
  sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' > backup_$(date +%F).sql

# restaurar sobre una base vacía:
docker compose -f docker-compose.prod.yml exec -T postgres \
  sh -c 'psql -U "$POSTGRES_USER" "$POSTGRES_DB"' < backup_2026-11-10.sql
```

Guardá los backups fuera de la VM (por ejemplo, S3). Eso queda fuera de este compose.

## 8. IA local (opcional)

```bash
docker compose -f docker-compose.prod.yml --profile ollama up -d
docker compose -f docker-compose.prod.yml exec ollama ollama pull qwen2.5-coder:3b
```

Sin GPU, un modelo de 3B en CPU responde lento (cada llamada tiene un tope de 25 s antes de pasar al siguiente motor). Para producción conviene `AI_PROVIDER_MODE=gemini` con `GEMINI_API_KEY`.

## Límites conocidos

- **Un solo worker, a propósito.** Las salas en tiempo real, los locks, los rate limits y los circuit breakers viven en la memoria del proceso. No uses `--workers` ni réplicas del backend. Escalar horizontalmente exigiría primero un backplane compartido (por ejemplo Redis), que hoy no existe.
- **Rate limits por proceso**, no globales.
- **El WebSocket envía el JWT en la URL** (`?token=`). nginx y uvicorn lo ocultan en sus logs, pero queda en el historial del navegador de cada usuario.
- **`/generate?verify=true`** (compilar el proyecto Spring Boot generado) necesita Java y Maven, que la imagen no trae. El valor por defecto es `false` y funciona.
- **Base externa (RDS):** el compose arma `DATABASE_URL` con el Postgres interno. Para usar RDS hay que editar esa línea del servicio `backend` y quitar `postgres`.
- **Sin CI/CD:** el despliegue es manual con `git pull` y `up -d --build`.

## Problemas frecuentes

| Síntoma | Causa |
|---|---|
| `backend` en `Exited` con "Refusing to start in production" | Falta `JWT_SECRET`, es corto o es el de ejemplo |
| `frontend` no arranca y `backend` no está `healthy` | Mirá `docker compose logs backend`; suele ser la conexión a Postgres |
| El build del frontend muere con "Killed" | Falta memoria: agregá swap o usá `t3.medium` |
| Certbot falla el desafío | El puerto 80 no está abierto en el Security Group o el DNS no apunta a la IP |
