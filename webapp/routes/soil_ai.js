const express = require("express");
const router = express.Router();
const { spawn } = require("child_process");
const path = require("path");
const multer = require("multer");
const fs = require("fs").promises;
const os = require("os");

// Import agricultural configuration from Python module
// To sync with Python config: npm run sync-config (or manually update these lists if Python config changes)
const AGRICULTURAL_CONFIG = {
    // Maharashtra districts
    MAHARASHTRA_DISTRICTS: [
        "Thane", "Pune", "Nashik", "Aurangabad", "Nagpur", "Kolhapur",
        "Satara", "Solapur", "Sangli", "Ahmednagar", "Jalgaon", "Dhule",
        "Nanded", "Latur", "Osmanabad", "Beed", "Jalna", "Parbhani",
        "Hingoli", "Washim", "Buldhana", "Akola", "Amravati", "Yavatmal",
        "Wardha", "Chandrapur", "Gadchiroli", "Bhandara", "Gondia", "Raigad",
        "Ratnagiri", "Sindhudurg"
    ],
    
    // Soil types
    SOIL_TYPES: ["Loamy", "Clayey", "Sandy", "Alluvial", "Black", "Red", "Laterite"],
    
    // Seasons
    SEASONS: ["Kharif", "Rabi", "Summer"],
    
    // Irrigation types
    IRRIGATION_TYPES: ["Rain-fed", "Irrigated"]
};

// Configure multer for file uploads
const upload = multer({
    dest: os.tmpdir(),
    limits: { fileSize: 10 * 1024 * 1024 }, // 10MB limit
    fileFilter: (req, file, cb) => {
        const allowedTypes = /\.(pdf|jpg|jpeg|png)$/i;
        if (allowedTypes.test(file.originalname)) {
            cb(null, true);
        } else {
            cb(new Error("Invalid file type. Only PDF, JPG, JPEG, PNG allowed."));
        }
    }
});

// Destructure config for easier access
const { MAHARASHTRA_DISTRICTS, SOIL_TYPES, SEASONS, IRRIGATION_TYPES } = AGRICULTURAL_CONFIG;

// POST: Extract text from uploaded file
router.post("/extract", upload.single("file"), async (req, res) => {
    if (!req.file) {
        return res.status(400).json({
            success: false,
            error: "No file uploaded"
        });
    }

    try {
        const filePath = req.file.path;
        const fileExtension = path.extname(req.file.originalname).toLowerCase();
        let extractedText = "";

        if (fileExtension === ".pdf") {
            // Extract text from PDF using Python
            extractedText = await extractPDFText(filePath);
        } else if ([".jpg", ".jpeg", ".png"].includes(fileExtension)) {
            // Extract text from image using OCR (Python)
            extractedText = await extractImageText(filePath);
        } else {
            throw new Error("Unsupported file type");
        }

        // Normalize extracted text
        extractedText = normalizeText(extractedText);

        // Clean up temp file
        await fs.unlink(filePath).catch(() => {});

        res.json({
            success: true,
            text: extractedText
        });
    } catch (error) {
        // Clean up temp file on error
        if (req.file && req.file.path) {
            await fs.unlink(req.file.path).catch(() => {});
        }
        console.error("File extraction error:", error);
        res.status(500).json({
            success: false,
            error: error.message || "Failed to extract text from file"
        });
    }
});

// Helper: Extract text from PDF
async function extractPDFText(filePath) {
    return new Promise((resolve, reject) => {
        const projectRoot = path.join(__dirname, "../..");
        const pythonScript = path.join(projectRoot, "extract_pdf.py");
        // Use venv Python interpreter (venv is at FarmChain/.venv)
        const pythonExe = path.join(projectRoot, "..", ".venv", "bin", "python");
        
        const pythonProcess = spawn(pythonExe, [pythonScript, filePath], {
            cwd: projectRoot
        });

        let output = "";
        let errorOutput = "";

        pythonProcess.stdout.on("data", (data) => {
            output += data.toString();
        });

        pythonProcess.stderr.on("data", (data) => {
            errorOutput += data.toString();
        });

        pythonProcess.on("close", (code) => {
            if (code !== 0) {
                reject(new Error(errorOutput || "PDF extraction failed"));
            } else {
                resolve(output.trim());
            }
        });
    });
}

// Helper: Extract text from image using OCR
async function extractImageText(filePath) {
    return new Promise((resolve, reject) => {
        const projectRoot = path.join(__dirname, "../..");
        const pythonScript = path.join(projectRoot, "extract_image.py");
        // Use venv Python interpreter (venv is at FarmChain/.venv)
        const pythonExe = path.join(projectRoot, "..", ".venv", "bin", "python");
        
        const pythonProcess = spawn(pythonExe, [pythonScript, filePath], {
            cwd: projectRoot
        });

        let output = "";
        let errorOutput = "";

        pythonProcess.stdout.on("data", (data) => {
            output += data.toString();
        });

        pythonProcess.stderr.on("data", (data) => {
            errorOutput += data.toString();
        });

        pythonProcess.on("close", (code) => {
            if (code !== 0) {
                reject(new Error(errorOutput || "OCR extraction failed"));
            } else {
                resolve(output.trim());
            }
        });
    });
}

// Helper: Normalize extracted text
function normalizeText(text) {
    if (!text) return "";
    
    // Remove headers/footers (common patterns)
    text = text.replace(/Page \d+ of \d+/gi, "");
    text = text.replace(/^\d+\s*$/gm, ""); // Page numbers
    
    // Collapse multiple whitespace
    text = text.replace(/\s+/g, " ");
    
    // Remove excessive newlines
    text = text.replace(/\n{3,}/g, "\n\n");
    
    // Preserve numeric values and units (keep patterns like "7.8", "215 kg/ha")
    // This is already preserved by the above operations
    
    return text.trim();
}

// POST: Process soil report (optional auth)
router.post("/analyze", async (req, res) => {
    try {
        const { report_text, district, soil_type, irrigation_type, season, language } = req.body;

        // FIX 1: Validation - district, season, and irrigation_type are required
        if (!report_text || !district || !season || !irrigation_type) {
            return res.status(400).json({
                success: false,
                error: "Report text, district, season, and irrigation type are required"
            });
        }

        if (!MAHARASHTRA_DISTRICTS.includes(district)) {
            return res.status(400).json({
                success: false,
                error: "Invalid district. Must be a Maharashtra district."
            });
        }

        if (!SEASONS.includes(season)) {
            return res.status(400).json({
                success: false,
                error: "Invalid season. Must be one of: " + SEASONS.join(", ")
            });
        }

        if (!IRRIGATION_TYPES.includes(irrigation_type)) {
            return res.status(400).json({
                success: false,
                error: "Invalid irrigation type. Must be one of: " + IRRIGATION_TYPES.join(", ")
            });
        }

        // Call Python API script via stdin
        const projectRoot = path.join(__dirname, "../..");
        const pythonScript = path.join(projectRoot, "soil_ai_api.py");
        const venvPython = path.join(projectRoot, "..", ".venv", "bin", "python");
        const pythonExe = require("fs").existsSync(venvPython) ? venvPython : "python3";
        
        const inputData = JSON.stringify({
            report_text: report_text,
            district: district,
            soil_type: soil_type || null,
            irrigation_type: irrigation_type || "Rain-fed",
            season: season || "Kharif",
            language: language || "marathi"
        });
        
        const pythonProcess = spawn(pythonExe, [pythonScript], {
            cwd: projectRoot,
            stdio: ['pipe', 'pipe', 'pipe']
        });

        let output = "";
        let errorOutput = "";

        // Hard timeout to avoid hanging when model download or env issues occur
        const timeoutMs = 60000; // 60s
        const timeout = setTimeout(() => {
            console.error("Soil AI python timed out after", timeoutMs, "ms");
            pythonProcess.kill("SIGKILL");
        }, timeoutMs);

        pythonProcess.on("error", (err) => {
            clearTimeout(timeout);
            console.error("Failed to start python process:", err);
            return res.status(500).json({
                success: false,
                error: "Failed to start soil AI process",
                details: err.message
            });
        });

        // Write input data to stdin
        pythonProcess.stdin.write(inputData);
        pythonProcess.stdin.end();

        pythonProcess.stdout.on("data", (data) => {
            output += data.toString();
        });

        pythonProcess.stderr.on("data", (data) => {
            const stderrData = data.toString();
            errorOutput += stderrData;
        });

        pythonProcess.on("close", (code) => {
            clearTimeout(timeout);
            if (code !== 0) {
                console.error("Python process exited with error code:", code);
                console.error("Python error output:", errorOutput);
                return res.status(500).json({
                    success: false,
                    error: "Error processing soil report",
                    details: errorOutput,
                    explanation: {
                        summary: "An error occurred while processing the soil report.",
                        disclaimer: "This recommendation is based on soil reports, district conditions, and standard agriculture guidelines. Please consult your local agriculture officer for final decisions."
                    }
                });
            }

            try {
                const result = JSON.parse(output.trim());
                
                // SAFETY CHECK: Ensure explanation is always present
                if (!result.explanation) {
                    result.explanation = {
                        summary: "Unable to generate explanation.",
                        disclaimer: "This recommendation is based on soil reports, district conditions, and standard agriculture guidelines. Please consult your local agriculture officer for final decisions."
                    };
                }
                
                // Ensure summary exists in explanation
                if (!result.explanation.summary && !result.explanation.content) {
                    result.explanation.summary = "Unable to generate explanation summary.";
                }
                
                res.json(result);
            } catch (parseError) {
                console.error("Parse error:", parseError);
                res.status(500).json({
                    success: false,
                    error: "Error parsing response",
                    details: output,
                    explanation: {
                        summary: "An error occurred while parsing the response.",
                        disclaimer: "This recommendation is based on soil reports, district conditions, and standard agriculture guidelines. Please consult your local agriculture officer for final decisions."
                    }
                });
            }
        });

    } catch (error) {
        console.error("Error in soil AI route:", error);
        res.status(500).json({
            success: false,
            error: error.message,
            explanation: {
                summary: "An error occurred while processing the request.",
                disclaimer: "This recommendation is based on soil reports, district conditions, and standard agriculture guidelines. Please consult your local agriculture officer for final decisions."
            }
        });
    }
});

// POST: Lightweight chat with Ollama (Maharashtra farming expert)
router.post("/chat", async (req, res) => {
    try {
        const { message, context, language } = req.body || {};
        if (!message || !message.trim()) {
            return res.status(400).json({ success: false, error: "Message is required" });
        }

        const lang = (language || 'english').toLowerCase();
        const isMarathi = lang.includes('marathi');
        
        const systemPrompt = isMarathi
            ? "तुम्ही एक मराठी कृषी विशेषज्ञ आहात. महाराष्ट्रातील शेतीबाड़ी, पिके, माती, सिंचन आणि उत्पादन तंत्रांविषयी छोटे, सरळ उत्तरे द्या. केवळ मराठीतच उत्तर द्या. इतर भाषांचा वापर करू नका. शेतकऱ्यांसाठी व्यावहारिक सल्ला द्या."
            : "You are a concise, practical Maharashtra farming expert. Answer only in English about crops, soil, irrigation, and farming best practices in Maharashtra. Keep answers short and actionable. Do not use other languages. Focus on English only.";
        
        const userContent = context ? `Context: ${context}\nQuestion: ${message}` : message;

        const ollamaUrl = "http://localhost:11434/api/chat";
        const response = await fetch(ollamaUrl, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                model: "llama3.2",
                messages: [
                    { role: "system", content: systemPrompt },
                    { role: "user", content: userContent }
                ],
                options: { temperature: 0.2 },
                stream: false
            })
        });

        const raw = await response.text();
        if (!response.ok) {
            return res.status(500).json({ success: false, error: "Ollama error", details: raw });
        }

        // Ollama might send multiple JSON objects when streaming is mis-set; guard parse
        let data = {};
        try {
            data = JSON.parse(raw.trim().split("\n").filter(Boolean).pop() || "{}" );
        } catch (e) {
            return res.status(500).json({ success: false, error: "Parse error", details: raw });
        }

        const reply = data?.message?.content || "";
        return res.json({ success: true, reply });
    } catch (error) {
        console.error("Chat error:", error);
        return res.status(500).json({ success: false, error: error.message || "Chat failed" });
    }
});

module.exports = router;
