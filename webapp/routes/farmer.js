const express = require("express");
const router = express.Router();
const Listing = require("../models/Listing");
const Booking = require("../models/Booking");
const User = require("../models/User");
const { verifyToken, requireFarmer } = require("../middleware/auth");
const fs = require('fs');
const path = require('path');
const os = require("os");
const multer = require("multer");

// ----------------- FARMER ROUTES -----------------
const REGIONS = ["Thane", "Pune", "Nashik", "Aurangabad", "Nagpur", "Kolhapur", "Satara", "Solapur"];
const CATEGORIES = ["Tractor", "Rotavator", "Seeder", "Harvester", "Sprayer", "Tiller", "Baler"];
const CATEGORY_TRANSLATIONS = {
    'Tractor': 'ट्रॅक्टर',
    'Rotavator': 'रोटावेटर',
    'Seeder': 'सीडर',
    'Harvester': 'हार्वेस्टर',
    'Sprayer': 'स्प्रेयर',
    'Tiller': 'टिलर',
    'Baler': 'बेलर'
};

// Ensure orders directory exists
const ordersDir = path.join(__dirname, '../orders');
if (!fs.existsSync(ordersDir)) fs.mkdirSync(ordersDir);

// ---------- OLD WORKING ROUTES ----------
router.get("/", verifyToken, requireFarmer, async (req, res) => {
    try {
        const user = await User.findById(req.user.userId);
        if (!user) {
            res.clearCookie('token');
            return res.redirect('/auth/login/farmer');
        }

        const farmerName = user.name;
        const region = req.query.region ? req.query.region.trim() : "";
        const category = req.query.category ? req.query.category.trim() : "";
        const query = req.query.q ? req.query.q.trim() : "";
        const lang = req.query.lang || 'en';

        let filter = { status: "approved" };
        if (region) filter.region = { $regex: new RegExp(`^${region}`, "i") };
        if (category && category !== "all") filter.category = { $regex: new RegExp(`^${category}`, "i") };
        if (query) filter.name = { $regex: query, $options: "i" };

        const listings = await Listing.find(filter).populate("owner");
        let bookings = await Booking.find({ farmer: req.user.userId }).populate({
            path: "listing",
            populate: { path: "owner" }
        });

        bookings = bookings.filter(b => b.listing !== null);

        res.render("farmer", {
            farmerName,
            listings,
            bookings,
            REGIONS,
            CATEGORIES,
            region,
            category: category || "all",
            query,
            lang,
            CATEGORY_TRANSLATIONS
        });
    } catch (error) {
        console.error("Error fetching listings:", error);
        res.status(500).send("Error fetching listings");
    }
});

router.post('/initiate-booking/:listingId', verifyToken, requireFarmer, async (req, res) => {
    const { days } = req.body;
    const lang = req.query.lang || 'en';
    try {
        const listing = await Listing.findById(req.params.listingId);
        if (!listing) return res.status(404).send('Listing not found');

        const amount = listing.pricePerDay * Math.max(1, Number(days));
        const booking = await Booking.create({
            listing: listing._id,
            farmer: req.user.userId,
            from: new Date(),
            to: new Date(Date.now() + 86400000 * Math.max(1, Number(days))),
            amount,
            status: 'pending'
        });

        res.redirect(`/farmer/payment/${booking._id}?lang=${lang}`);
    } catch (error) {
        console.error('Error initiating booking:', error);
        res.status(500).send('Error initiating booking');
    }
});

router.get('/payment/:bookingId', verifyToken, requireFarmer, async (req, res) => {
    try {
        const booking = await Booking.findById(req.params.bookingId).populate('listing');
        if (!booking || booking.farmer.toString() !== req.user.userId) return res.status(404).send('Booking not found');

        res.render('payment', {
            bookingId: booking._id,
            listing: { ...booking.listing._doc, onChainId: booking.listing.onChainId || 0 },
            days: Math.round((booking.to - booking.from) / 86400000),
            amount: booking.amount
        });
    } catch (error) {
        console.error('Error loading payment:', error);
        res.status(500).send('Error loading payment');
    }
});

router.post('/confirm-booking/:bookingId', verifyToken, requireFarmer, async (req, res) => {
    const { txHash } = req.body;
    try {
        const booking = await Booking.findById(req.params.bookingId).populate('listing');
        if (!booking || booking.farmer.toString() !== req.user.userId) return res.status(404).send('Booking not found');

        booking.status = 'confirmed';
        booking.txHash = txHash;
        await booking.save();

        const farmerId = req.user.userId;
        const orderFile = path.join(ordersDir, `farmer_${farmerId}.json`);

        let orders = [];
        if (fs.existsSync(orderFile)) orders = JSON.parse(fs.readFileSync(orderFile, 'utf8'));

        orders.push({
            bookingId: req.params.bookingId,
            txHash,
            amount: booking.amount,
            days: Math.round((booking.to - booking.from) / 86400000),
            listingId: booking.listing.onChainId,
            listingName: booking.listing.name,
            createdAt: new Date().toISOString()
        });

        fs.writeFileSync(orderFile, JSON.stringify(orders, null, 2));
        res.status(200).send('Confirmed');
    } catch (error) {
        console.error('Error confirming booking:', error);
        res.status(500).send('Error confirming booking');
    }
});

router.get('/success/:bookingId', verifyToken, requireFarmer, async (req, res) => {
    try {
        const booking = await Booking.findById(req.params.bookingId).populate('listing');
        if (!booking || booking.farmer.toString() !== req.user.userId || booking.status !== 'confirmed') return res.status(404).send('Booking not found');

        res.render('success', {
            listing: booking.listing,
            days: Math.round((booking.to - booking.from) / 86400000),
            amount: booking.amount,
            txHash: booking.txHash
        });
    } catch (error) {
        console.error('Error loading success:', error);
        res.status(500).send('Error loading success');
    }
});

router.get("/analytics", verifyToken, requireFarmer, async (req, res) => {
    const bookings = await Booking.find({ farmer: req.user.userId, status: "confirmed" }).populate("listing");

    let totalSpent = 0, totalDays = 0, usageMap = {};
    bookings.forEach(b => {
        totalSpent += b.amount;
        const days = Math.round((b.to - b.from) / 86400000);
        totalDays += days;
        usageMap[b.listing?.name || "Unknown"] = (usageMap[b.listing?.name] || 0) + days;
    });

    res.render("analytics-farmer", { totalSpent, totalDays, usageMap });
});

// ----------------- SOIL-AI / PYTHON ROUTES -----------------

const upload = multer({ dest: os.tmpdir(), limits: { fileSize: 10*1024*1024 }, fileFilter: (req, file, cb) => {
    if (/\.(pdf|jpg|jpeg|png)$/i.test(file.originalname)) cb(null, true);
    else cb(new Error("Only PDF/JPG/PNG allowed"));
}});

router.post("/extract", upload.single("file"), async (req, res) => {
    if (!req.file) return res.status(400).json({ success: false, error: "No file uploaded" });
    try {
        const filePath = req.file.path;
        const fileExt = path.extname(req.file.originalname).toLowerCase();
        let extractedText = "";

        const extractScript = fileExt === ".pdf" ? "extract_pdf.py" : "extract_image.py";
        const projectRoot = path.join(__dirname, "../..");
        const pythonScript = path.join(projectRoot, extractScript);
        const venvPython = path.join(projectRoot, "..", ".venv", "bin", "python");
        const pythonCmd = fs.existsSync(venvPython) ? venvPython : "python3";

        const { spawn } = require("child_process");
        const pythonProcess = spawn(pythonCmd, [pythonScript, filePath], { cwd: projectRoot });

        let output = "", errorOutput = "";
        pythonProcess.stdout.on("data", d => output += d.toString());
        pythonProcess.stderr.on("data", d => errorOutput += d.toString());
        pythonProcess.on("close", code => {
            fs.unlink(filePath).catch(()=>{});
            if (code !== 0) return res.status(500).json({ success:false, error:errorOutput||"Extraction failed" });
            extractedText = output.trim().replace(/Page \d+ of \d+/gi,"").replace(/^\d+\s*$/gm,"").replace(/\s+/g," ").replace(/\n{3,}/g,"\n\n").trim();
            res.json({ success:true, text: extractedText });
        });

    } catch(err) {
        if (req.file && req.file.path) fs.unlink(req.file.path).catch(()=>{});
        res.status(500).json({ success:false, error: err.message });
    }
});

router.post("/analyze", async (req, res) => {
    try {
        const { report_text, district, soil_type, irrigation_type, season, language } = req.body;
        if (!report_text || !district || !season || !irrigation_type)
            return res.status(400).json({ success:false, error:"Required fields missing" });

        const projectRoot = path.join(__dirname, "../..");
        const pythonScript = path.join(projectRoot, "soil_ai_api.py");
        const venvPython = path.join(projectRoot, "..", ".venv", "bin", "python");
        const pythonExe = fs.existsSync(venvPython) ? venvPython : "python3";

        const inputData = JSON.stringify({ report_text, district, soil_type: soil_type||null, irrigation_type, season, language: language||"marathi" });
        const pythonProcess = spawn(pythonExe, [pythonScript], { cwd: projectRoot, stdio:['pipe','pipe','pipe'] });

        let output = "", errorOutput = "";
        const timeout = setTimeout(()=>pythonProcess.kill("SIGKILL"),60000);

        pythonProcess.stdin.write(inputData); pythonProcess.stdin.end();
        pythonProcess.stdout.on("data", d=>output+=d.toString());
        pythonProcess.stderr.on("data", d=>errorOutput+=d.toString());

        pythonProcess.on("close", code=>{
            clearTimeout(timeout);
            if(code!==0) return res.status(500).json({ success:false, error:"Error processing soil report", details:errorOutput });
            try{
                const result = JSON.parse(output.trim());
                if(!result.explanation) result.explanation = { summary:"Unable to generate explanation", disclaimer:"Consult local agriculture officer" };
                res.json(result);
            }catch(e){
                res.status(500).json({ success:false, error:"Parsing error", details: output });
            }
        });
    } catch(err){
        res.status(500).json({ success:false, error:err.message });
    }
});

router.post("/chat", async (req,res)=>{
    try{
        const { message, context, language } = req.body || {};
        if(!message || !message.trim()) return res.status(400).json({ success:false, error:"Message is required" });

        const lang = (language||"english").toLowerCase();
        const isMarathi = lang.includes("marathi");
        const systemPrompt = isMarathi 
            ? "आपण एक कृषी सहायक चॅटबॉट आहात. आपला नाव 'Rohan' नाही. आपल्याला 15 वर्षांचा अनुभव नाही. आपण एक मशीन आहात. केवळ प्रश्नाचे उत्तर द्या. महाराष्ट्रातील शेतीविषयी व्यावहारिक सल्ला द्या. छोटे उत्तरे द्या."
            : "You are an agricultural assistant chatbot. You are NOT named Rohan. You do NOT have 15 years of experience. You are a machine. Just answer the farming question about Maharashtra concisely.";

        const userContent = context ? `Context: ${context}\nQuestion: ${message}` : message;

        const response = await fetch("http://localhost:11434/api/chat",{
            method:"POST",
            headers:{"Content-Type":"application/json"},
            body:JSON.stringify({
                model:"llama3.2",
                messages:[{role:"system", content:systemPrompt},{role:"user", content:userContent}],
                options:{temperature:0.2}, stream:false
            })
        });

        const raw = await response.text();
        if(!response.ok) return res.status(500).json({ success:false, error:"Ollama error", details:raw });
        let data={};
        try{ data = JSON.parse(raw.trim().split("\n").filter(Boolean).pop()||"{}"); }catch(e){ return res.status(500).json({success:false,error:"Parse error",details:raw}); }

        const reply = data?.message?.content||"";
        return res.json({ success:true, reply });
    }catch(err){ res.status(500).json({ success:false, error:err.message }); }
});

module.exports = router;
