# Ollama Setup for FarmChain Soil AI

## Quick Start

1. **Install Ollama** (if not already installed):
   ```bash
   # macOS
   brew install ollama
   
   # Or download from: https://ollama.ai
   ```

2. **Start Ollama** (if not running):
   ```bash
   ollama serve
   ```
   Or it may already be running in the background.

3. **Pull a model** (choose one):
   ```bash
   # Recommended for this use case
   ollama pull llama3.2
   
   # Or alternatives:
   ollama pull llama3.1
   ollama pull mistral
   ollama pull qwen2.5
   ```

4. **Verify Ollama is running**:
   ```bash
   curl http://localhost:11434/api/tags
   ```
   Should return a list of available models.

5. **Update model name in code** (if using different model):
   Edit `soil_ai_module.py` line 12:
   ```python
   model="llama3.2",  # Change to your model name
   ```

## Configuration

- **Default Port**: `http://localhost:11434`
- **Default Model**: `llama3.2`
- **Location**: `soil_ai_module.py` line 11-14

## Troubleshooting

- **Connection Error**: Make sure Ollama is running (`ollama serve`)
- **Model Not Found**: Pull the model first (`ollama pull <model-name>`)
- **Port Changed**: Update `base_url` in `soil_ai_module.py`
