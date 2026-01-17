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
        
        const pythonProcess = spawn("python3", [pythonScript, filePath], {
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
        
        const pythonProcess = spawn("python3", [pythonScript, filePath], {
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
        
        const inputData = JSON.stringify({
            report_text: report_text,
            district: district,
            soil_type: soil_type || null,
            irrigation_type: irrigation_type || "Rain-fed",
            season: season || "Kharif",
            language: language || "marathi"
        });
        
        const pythonProcess = spawn("python3", [pythonScript], {
            cwd: projectRoot,
            stdio: ['pipe', 'pipe', 'pipe']
        });

        let output = "";
        let errorOutput = "";

        // Write input data to stdin
        pythonProcess.stdin.write(inputData);
        pythonProcess.stdin.end();

        pythonProcess.stdout.on("data", (data) => {
            output += data.toString();
        });

        pythonProcess.stderr.on("data", (data) => {
            const stderrData = data.toString();
            errorOutput += stderrData;
            // Always log Python stderr (contains debug messages)
            console.error("[Python stderr]", stderrData);
        });

        pythonProcess.on("close", (code) => {
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

            // Log stderr even on success (for debug messages)
            if (errorOutput) {
                console.log("[Python debug output]:", errorOutput);
            }

            try {
                const result = JSON.parse(output.trim());
                
                // DEBUG: Log the raw result from Python
                console.log("🔍 [Node.js] Raw Python result keys:", Object.keys(result));
                console.log("🔍 [Node.js] Has explanation:", !!result.explanation);
                if (result.explanation) {
                    console.log("🔍 [Node.js] Explanation keys:", Object.keys(result.explanation));
                    console.log("🔍 [Node.js] Summary:", result.explanation.summary);
                } else {
                    console.error("❌ [Node.js] CRITICAL: Explanation missing from Python response!");
                    console.error("❌ [Node.js] Full result:", JSON.stringify(result, null, 2));
                }
                
                // SAFETY CHECK: Ensure explanation is always present
                if (!result.explanation) {
                    console.error("❌ CRITICAL: Explanation missing from backend response!");
                    console.error("Response keys:", Object.keys(result));
                    // Add minimal explanation if missing
                    result.explanation = {
                        summary: "Unable to generate explanation.",
                        disclaimer: "This recommendation is based on soil reports, district conditions, and standard agriculture guidelines. Please consult your local agriculture officer for final decisions."
                    };
                }
                
                // Ensure summary exists in explanation
                if (!result.explanation.summary && !result.explanation.content) {
                    console.error("❌ CRITICAL: Explanation summary missing!");
                    result.explanation.summary = "Unable to generate explanation summary.";
                }
                
                // FINAL VERIFICATION: Log before sending to frontend
                console.log("🔍 [Node.js] FINAL RESULT being sent:", {
                    has_explanation: !!result.explanation,
                    explanation_keys: result.explanation ? Object.keys(result.explanation) : [],
                    has_summary: !!result.explanation?.summary,
                    summary_preview: result.explanation?.summary?.substring(0, 50) || "MISSING"
                });
                
                // #region agent log
                // Debug logging - fail silently if endpoint unavailable (prevents 404 console errors)
                try {
                  fetch('http://127.0.0.1:7242/ingest/ace7d4ff-0dbd-4417-a3b5-c830b918565a',{
                    method:'POST',
                    headers:{'Content-Type':'application/json'},
                    body:JSON.stringify({
                      location:'soil_ai.js:261',
                      message:'BEFORE res.json: result explanation check',
                      data:{
                        has_explanation:!!result.explanation,
                        explanation_keys:result.explanation?Object.keys(result.explanation):[],
                        has_summary:!!result.explanation?.summary,
                        has_advisory:!!result.explanation?.advisory,
                        timestamp:Date.now(),
                        sessionId:'debug-session',
                        runId:'run1',
                        hypothesisId:'C'
                      }
                    }),
                    signal:AbortSignal.timeout(100) // Timeout quickly to avoid hanging
                  }).catch(()=>{}); // Silently handle errors
                } catch(e) {} // Ignore any fetch errors
                // #endregion
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

module.exports = router;
