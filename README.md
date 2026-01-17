# FarmChain

Smart agriculture platform combining equipment rental, soil intelligence, and multilingual farmer experiences.

## At a Glance
- Roles: Farmer, Seller, Admin (JWT-secured)
- AI: Soil report parsing + fertilizer/crop guidance (Ollama Llama 3.2 + Gemini 2.0)
- UX: English/Marathi UI, speech support, responsive layouts
- Data: MongoDB for users/listings; on-disk uploads for images
- Infra: Node/Express web app, Python AI worker, Hardhat contracts scaffold

## Architecture
- Web app: `webapp/server.js` (Express, EJS views, Multer uploads, JWT auth)
- Soil AI service: Python (`soil_ai_module.py`, `soil_ai_api.py`) invoked from Node via stdin/stdout
- Frontend assets: `webapp/public` (CSS/JS) and `webapp/views` (EJS templates)
- Smart-contract scaffold: Hardhat config + `contracts/FarmMachinery.sol`

## Tech Stack
- Backend: Node 18+, Express 5, Mongoose, JWT, Multer, Bcrypt
- Frontend: EJS, vanilla JS, Tailwind CSS
- AI: Python 3.9+, LangChain, Ollama (llama3.2), Google Generative AI (gemini-2.0-flash)
- DB: MongoDB 6+
- Tooling: Hardhat, Nodemon, npm

## Prerequisites
- Node 18+
- Python 3.9+
- MongoDB running locally (default: mongodb://localhost:27017/farmrent)
- Ollama running with model `llama3.2` pulled
- Google Generative AI API key (for Gemini)

## Quick Start
From repo root:

```bash
# 1) Install Node deps
cd webapp
npm install

# 2) Install Python deps
cd ..


# 3) Environment (examples)
cp webapp/.env.example webapp/.env 2>/dev/null || true
cp .env.example .env 2>/dev/null || true

# Minimum required
cat <<'EOF' > webapp/.env
JWT_SECRET=change-me
MONGODB_URI=mongodb://localhost:27017/farmrent
EOF

cat <<'EOF' > .env
GOOGLE_API_KEY=replace-with-your-key
EOF

# 4) Start services
# Terminal A: MongoDB (if not already running)
# Terminal B: Node web app
cd webapp
npm start

# Terminal C: (optional) Streamlit equipment agent demo
cd ..
streamlit run app.py
```

Visit: http://localhost:3000

## Soil AI Workflow
1) Upload soil report (PDF/image) -> OCR/parse via Python
2) Extract parameters (pH, N, P, K, Organic Carbon)
3) Classify with thresholds from `agricultural_config.py`
4) Generate crop/fertilizer/equipment guidance (LLM + hard rules)
5) Return JSON + farmer-friendly explanation to the web app

### Key Files
- `agricultural_config.py` - single source of truth for thresholds, crops, districts, disclaimers
- `soil_ai_module.py` - extraction, classification, recommendations, explanations
- `webapp/routes/soil_ai.js` - file upload + bridge to Python AI

## Environment Variables

### webapp/.env
- `JWT_SECRET` (required)
- `MONGODB_URI` (default: mongodb://localhost:27017/farmrent)

### .env (project root, AI)
- `GOOGLE_API_KEY` (required for Gemini)
- Ollama: ensure `ollama serve` running and model `llama3.2` pulled

## NPM Scripts (webapp)
- `npm start` - run Express server
- `npm run dev` - dev mode with nodemon
- `npm test` - placeholder

## Python Utilities
- `python3 soil_ai_api.py` - stdin/stdout bridge used by Node
- `python3 -m py_compile soil_ai_module.py` - quick syntax check

## API Surface (selected)
- POST `/auth/login/:role` - Login (farmer/seller/admin)
- POST `/auth/signup/:role` - Register (farmer/seller)
- GET  `/farmer`, `/seller`, `/admin` - Role dashboards
- POST `/soil-ai/extract` - OCR/PDF text extraction
- POST `/soil-ai/analyze` - Soil analysis + recommendations

## Testing Checklist
- MongoDB running locally
- `npm start` in `webapp/`
- For AI flows: Ollama running, `GOOGLE_API_KEY` set, Python deps installed
- Optional: `python3 -m py_compile soil_ai_module.py`

## Contributing
- Fork -> branch -> PR
- Keep linting/formatting consistent
- Add tests or samples for new features

## License
MIT
npm install
