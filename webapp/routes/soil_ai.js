const express = require("express");
const router = express.Router();
const { spawn } = require("child_process");
const path = require("path");
const multer = require("multer");
const fs = require("fs"); // for existsSync, mkdirSync, etc.
const fsp = require("fs").promises; // for async unlink, readFile, writeFile
const os = require("os");

// DEBUG: Check if fs is loaded correctly
console.log("=== FS MODULE DEBUG ===");
console.log("fs module loaded:", !!fs);
console.log("fs.existsSync type:", typeof fs.existsSync);
console.log("fs keys:", Object.keys(fs).slice(0, 10));
console.log("======================");

// Import agricultural configuration
const AGRICULTURAL_CONFIG = {
    MAHARASHTRA_DISTRICTS: [
        "Thane", "Pune", "Nashik", "Aurangabad", "Nagpur", "Kolhapur",
        "Satara", "Solapur", "Sangli", "Ahmednagar", "Jalgaon", "Dhule",
        "Nanded", "Latur", "Osmanabad", "Beed", "Jalna", "Parbhani",
        "Hingoli", "Washim", "Buldhana", "Akola", "Amravati", "Yavatmal",
        "Wardha", "Chandrapur", "Gadchiroli", "Bhandara", "Gondia", "Raigad",
        "Ratnagiri", "Sindhudurg"
    ],
    SOIL_TYPES: ["Loamy", "Clayey", "Sandy", "Alluvial", "Black", "Red", "Laterite"],
    SEASONS: ["Kharif", "Rabi", "Summer"],
    IRRIGATION_TYPES: ["Rain-fed", "Irrigated"]
};

// Configure multer for uploads
const upload = multer({
    dest: os.tmpdir(),
    limits: { fileSize: 10 * 1024 * 1024 },
    fileFilter: (req, file, cb) => {
        const allowedTypes = /\.(pdf|jpg|jpeg|png)$/i;
        if (allowedTypes.test(file.originalname)) cb(null, true);
        else cb(new Error("Invalid file type. Only PDF, JPG, JPEG, PNG allowed."));
    }
});

const { MAHARASHTRA_DISTRICTS, SOIL_TYPES, SEASONS, IRRIGATION_TYPES } = AGRICULTURAL_CONFIG;

// ---------- KEEP OLD GET ROUTE ----------
router.get("/", (req, res) => {
    res.render("soil-ai", { lang: req.query.lang || "en" });
});

// POST: Extract text from uploaded file
router.post("/extract", upload.single("file"), async (req, res) => {
    console.log("=== EXTRACT ROUTE CALLED ===");
    // Declare filePath outside try block so it's accessible in catch
    let filePath = null;
    
    if (!req.file) {
        console.log("No file uploaded");
        return res.status(400).json({ success: false, error: "No file uploaded" });
    }

    try {
        console.log("File received:", req.file.originalname);
        filePath = req.file.path;
        console.log("File path:", filePath);
        
        const fileExtension = path.extname(req.file.originalname).toLowerCase();
        console.log("File extension:", fileExtension);
        
        let extractedText = "";

        if (fileExtension === ".pdf") {
            console.log("Extracting PDF text...");
            extractedText = await extractPDFText(filePath);
        } else if ([".jpg", ".jpeg", ".png"].includes(fileExtension)) {
            console.log("Extracting image text...");
            extractedText = await extractImageText(filePath);
        } else {
            throw new Error("Unsupported file type");
        }

        console.log("Text extracted, length:", extractedText.length);
        extractedText = normalizeText(extractedText);
        console.log("Text normalized, cleaning up file...");
        
        await fsp.unlink(filePath).catch((err) => {
            console.log("Cleanup error (non-fatal):", err.message);
        });

        console.log("Sending success response");
        res.json({ success: true, text: extractedText });
    } catch (error) {
        console.error("=== ERROR IN EXTRACT ROUTE ===");
        console.error("Error message:", error.message);
        console.error("Error stack:", error.stack);
        console.error("FilePath for cleanup:", filePath);
        
        // Clean up file if it exists
        if (filePath) {
            await fsp.unlink(filePath).catch((err) => {
                console.log("Cleanup error in catch:", err.message);
            });
        }

        res.status(500).json({ 
            success: false, 
            error: error.message || "Failed to extract text from file",
            debug: {
                errorType: error.constructor.name,
                filePath: filePath
            }
        });
    }
});

// Helper: Extract text from PDF
async function extractPDFText(filePath) {
    console.log("=== extractPDFText called ===");
    console.log("Input filePath:", filePath);
    
    return new Promise((resolve, reject) => {
        try {
            const projectRoot = path.join(__dirname, "../..");
            console.log("Project root:", projectRoot);
            
            const pythonScript = path.join(projectRoot, "extract_pdf.py");
            console.log("Python script path:", pythonScript);
            
            const venvPython = path.join(projectRoot, "..", ".venv", "bin", "python");
            console.log("Venv python path:", venvPython);
            console.log("Checking if venv python exists...");
            console.log("fs object type:", typeof fs);
            console.log("fs.existsSync type:", typeof fs.existsSync);
            
            // This is where the error likely occurs
            const pythonCmd = fs.existsSync(venvPython) ? venvPython : "python3";
            console.log("Python command to use:", pythonCmd);

            const pythonProcess = spawn(pythonCmd, [pythonScript, filePath], { cwd: projectRoot });

            let output = "", errorOutput = "";
            pythonProcess.stdout.on("data", data => output += data.toString());
            pythonProcess.stderr.on("data", data => errorOutput += data.toString());
            pythonProcess.on("close", code => {
                console.log("Python process closed with code:", code);
                if (code !== 0) {
                    console.error("Python error output:", errorOutput);
                    reject(new Error(errorOutput || "PDF extraction failed"));
                } else {
                    console.log("PDF extraction successful");
                    resolve(output.trim());
                }
            });
        } catch (err) {
            console.error("Error in extractPDFText setup:", err);
            reject(err);
        }
    });
}

// Helper: Extract text from image
async function extractImageText(filePath) {
    console.log("=== extractImageText called ===");
    console.log("Input filePath:", filePath);
    
    return new Promise((resolve, reject) => {
        try {
            const projectRoot = path.join(__dirname, "../..");
            console.log("Project root:", projectRoot);
            
            const pythonScript = path.join(projectRoot, "extract_image.py");
            console.log("Python script path:", pythonScript);
            
            const venvPython = path.join(projectRoot, "..", ".venv", "bin", "python");
            console.log("Venv python path:", venvPython);
            console.log("Checking if venv python exists...");
            
            // This is where the error likely occurs
            const pythonCmd = fs.existsSync(venvPython) ? venvPython : "python3";
            console.log("Python command to use:", pythonCmd);

            const pythonProcess = spawn(pythonCmd, [pythonScript, filePath], { cwd: projectRoot });

            let output = "", errorOutput = "";
            pythonProcess.stdout.on("data", data => output += data.toString());
            pythonProcess.stderr.on("data", data => errorOutput += data.toString());
            pythonProcess.on("close", code => {
                console.log("Python process closed with code:", code);
                if (code !== 0) {
                    console.error("Python error output:", errorOutput);
                    reject(new Error(errorOutput || "OCR extraction failed"));
                } else {
                    console.log("Image extraction successful");
                    resolve(output.trim());
                }
            });
        } catch (err) {
            console.error("Error in extractImageText setup:", err);
            reject(err);
        }
    });
}

// Helper: Normalize extracted text
function normalizeText(text) {
    if (!text) return "";
    text = text.replace(/Page \d+ of \d+/gi, "");
    text = text.replace(/^\d+\s*$/gm, "");
    text = text.replace(/\s+/g, " ");
    text = text.replace(/\n{3,}/g, "\n\n");
    return text.trim();
}

// POST: Process soil report
router.post("/analyze", async (req, res) => {
    console.log("=== ANALYZE ROUTE CALLED ===");
    try {
        const { report_text, district, soil_type, irrigation_type, season, language } = req.body;

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

        const projectRoot = path.join(__dirname, "../..");
        const pythonScript = path.join(projectRoot, "soil_ai_api.py");
        const venvPython = path.join(projectRoot, "..", ".venv", "bin", "python");
        
        console.log("Checking for venv python in analyze route...");
        const pythonExe = fs.existsSync(venvPython) ? venvPython : "python3";
        console.log("Using python:", pythonExe);

        const inputData = JSON.stringify({
            report_text,
            district,
            soil_type: soil_type || null,
            irrigation_type: irrigation_type || "Rain-fed",
            season: season || "Kharif",
            language: language || "marathi"
        });

        const pythonProcess = spawn(pythonExe, [pythonScript], { 
            cwd: projectRoot, 
            stdio: ['pipe', 'pipe', 'pipe'] 
        });

        let output = "", errorOutput = "";
        const timeoutMs = 60000;
        const timeout = setTimeout(() => { 
            pythonProcess.kill("SIGKILL"); 
            console.error("Soil AI python timed out"); 
        }, timeoutMs);

        pythonProcess.on("error", err => { 
            clearTimeout(timeout); 
            console.error("Python process error:", err);
            return res.status(500).json({ 
                success: false, 
                error: "Failed to start soil AI process", 
                details: err.message 
            }); 
        });

        pythonProcess.stdin.write(inputData); 
        pythonProcess.stdin.end();

        pythonProcess.stdout.on("data", data => output += data.toString());
        pythonProcess.stderr.on("data", data => errorOutput += data.toString());

        pythonProcess.on("close", code => {
            clearTimeout(timeout);
            console.log("Analyze python process closed with code:", code);
            
            if (code !== 0) {
                console.error("Python error in analyze:", errorOutput);
                return res.status(500).json({ 
                    success: false, 
                    error: "Error processing soil report", 
                    details: errorOutput, 
                    explanation: { 
                        summary: "Error occurred while processing soil report.", 
                        disclaimer: "Consult local agriculture officer for final decisions." 
                    } 
                });
            }

            try {
                const result = JSON.parse(output.trim());
                if (!result.explanation) {
                    result.explanation = { 
                        summary: "Unable to generate explanation.", 
                        disclaimer: "Consult local agriculture officer." 
                    };
                }
                if (!result.explanation.summary && !result.explanation.content) {
                    result.explanation.summary = "Unable to generate explanation summary.";
                }
                res.json(result);
            } catch (parseError) {
                console.error("Parse error in analyze:", parseError);
                res.status(500).json({ 
                    success: false, 
                    error: "Error parsing response", 
                    details: output, 
                    explanation: { 
                        summary: "Error occurred while parsing response.", 
                        disclaimer: "Consult local agriculture officer." 
                    } 
                });
            }
        });

    } catch (error) {
        console.error("Error in analyze route:", error);
        res.status(500).json({ 
            success: false, 
            error: error.message, 
            explanation: { 
                summary: "Error processing request.", 
                disclaimer: "Consult local agriculture officer." 
            } 
        });
    }
});

// POST: Lightweight chat with Ollama
router.post("/chat", async (req, res) => {
    console.log("=== CHAT ROUTE CALLED ===");
    try {
        const { message, context, language } = req.body || {};
        if (!message || !message.trim()) {
            return res.status(400).json({ 
                success: false, 
                error: "Message is required" 
            });
        }

        const lang = (language || 'english').toLowerCase();
        const isMarathi = lang.includes('marathi');

        const systemPrompt = isMarathi
            ? "आपण एक कृषी सहायक चॅटबॉट आहात. आपला नाव 'Rohan' नाही. आपल्याला 15 वर्षांचा अनुभव नाही. आपण एक मशीन आहात. केवळ प्रश्नाचे उत्तर द्या. आपले नाव, अनुभव, किंवा पार्श्वभूमी कधीही सांगू नका. महाराष्ट्रातील शेतीविषयी व्यावहारिक सल्ला द्या. छोटे उत्तरे द्या."
            : "You are an agricultural assistant chatbot. You are NOT named Rohan. You do NOT have 15 years of experience. You are a machine/AI. NEVER claim to be a farmer or person. NEVER mention your name, background, or experience. Just answer the question about farming in Maharashtra. Be concise. Focus only on the farming advice asked.";

        const userContent = context 
            ? `Context: ${context}\nQuestion: ${message}` 
            : message;

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
            console.error("Ollama error:", raw);
            return res.status(500).json({ 
                success: false, 
                error: "Ollama error", 
                details: raw 
            });
        }

        let data = {};
        try { 
            data = JSON.parse(raw.trim().split("\n").filter(Boolean).pop() || "{}"); 
        } catch (e) { 
            console.error("Parse error in chat:", e);
            return res.status(500).json({ 
                success: false, 
                error: "Parse error", 
                details: raw 
            }); 
        }

        const reply = data?.message?.content || "";
        return res.json({ success: true, reply });
    } catch (error) {
        console.error("Error in chat route:", error);
        return res.status(500).json({ 
            success: false, 
            error: error.message || "Chat failed" 
        });
    }
});

module.exports = router;