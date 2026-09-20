# Case2Code — Backend (FastAPI CASE Platform)

Backend principal del software CASE para modelado UML colaborativo y generación de código Spring Boot.

## Arquitectura

Implementado siguiendo principios de **Clean / Hexagonal Architecture** y **Command Pattern**:

```
Backend/
├── app/
│   ├── domain/              # Modelos canónicos y reglas de negocio puras
│   │   ├── models/          # CanonicalUmlDocument v1, Typed UML Commands
│   │   └── services/        # UmlValidator (reglas e integridad referencial)
│   ├── application/         # Casos de uso y orquestación
│   │   ├── command_bus.py   # Command Bus tipado
│   │   ├── command_handlers/# Handlers para cada comando UML
│   │   └── ports/           # Interfaces abstractas (DiagramRepositoryPort)
│   ├── infrastructure/      # Adaptadores técnicos
│   │   └── persistence/     # SQLAlchemy + PostgreSQL JSONB / SQLite fallback
│   ├── presentation/        # Controladores REST FastAPI
│   │   ├── routes/          # Endpoints (/api/diagrams, /api/diagrams/{id}/commands)
│   │   └── schemas/         # DTOs de entrada y salida
│   ├── core/                # Configuración y base de datos
│   └── main.py              # Punto de entrada de la aplicación FastAPI
└── tests/                   # Pruebas unitarias e integradas (pytest)
```

## Requisitos

- Python 3.12+
- Docker (opcional, para PostgreSQL 16 local con `docker-compose`)

## Puesta en marcha

1. Crear y activar entorno virtual:
   ```bash
   python -m venv .venv
   # Windows:
   .\.venv\Scripts\activate
   # Linux/macOS:
   source .venv/bin/activate
   ```

2. Instalar dependencias:
   ```bash
   pip install -r requirements.txt
   ```

3. (Opcional) Iniciar PostgreSQL:
   ```bash
   docker compose up -d postgres
   ```

4. Ejecutar el servidor de desarrollo:
   ```bash
   uvicorn app.main:app --reload --port 8000
   ```

5. Documentación interactiva Swagger:
   - `http://localhost:8000/api/docs`

## Ejecución de pruebas

```bash
pytest tests/ -v
```

