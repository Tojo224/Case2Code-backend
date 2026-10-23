# Local AI Provider Setup (Ollama / vLLM / Colab Worker)

Case2Code includes native support for local open-weights Large Language Models (LLMs) through `LocalLlmAiProvider`, compatible with both **Ollama** (`/api/chat` and `/v1/chat/completions`) and **OpenAI-compatible inference servers** (LM Studio, vLLM, Google Colab GPU workers).

## 1. Running Locally with Ollama

1. Install [Ollama](https://ollama.com).
2. Pull the base coding model and build the custom Case2Code Senior Architect model from the repository `Modelfile`:
   ```bash
   ollama pull qwen2.5-coder:3b
   ollama create case2code-senior -f ollama/Modelfile
   ```
3. Configure your `.env` (or use the in-app **Local AI Settings** panel inside the Multiplayer Chat):
   ```env
   AI_PROVIDER_MODE=hybrid
   LOCAL_LLM_BASE_URL=http://localhost:11434
   LOCAL_LLM_MODEL=qwen2.5-coder:3b
   ```

## 2. Running via Docker Compose

Start both PostgreSQL and the local Ollama container:
```bash
docker compose up -d
docker exec -it case2code_ollama ollama pull qwen2.5-coder:3b
docker exec -it case2code_ollama ollama create case2code-senior -f /ollama/Modelfile
```

## 3. Remote Local Worker (For Free-Tier Cloud Deployments with Limited RAM)

When deploying the FastAPI backend on memory-constrained free-tier servers (e.g., AWS `t2.micro` with 1 GB RAM), you can run the local LLM on your laptop or a free Google Colab T4 GPU and expose it via Cloudflare Tunnel:

```bash
# On your local machine or Google Colab:
ollama serve &
ollama pull qwen2.5-coder:3b
npx cloudflared tunnel --url http://localhost:11434
```

Paste the generated `https://xxxx.trycloudflare.com` URL into the **Local AI Settings** panel in the Case2Code Multiplayer Chat UI or set `LOCAL_LLM_BASE_URL` in your environment. If the local node goes offline, Case2Code's circuit breaker automatically falls back to Google Gemini or the built-in deterministic rule engine without interrupting the session.
