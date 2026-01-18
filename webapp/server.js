const express = require("express");
const mongoose = require("mongoose");
const path = require("path");
const bodyParser = require("body-parser");
const cookieParser = require("cookie-parser");
const jwt = require("jsonwebtoken");
const User = require("./models/User");
const authRoutes = require("./routes/auth");

const app = express();

app.use(express.json());
app.use(cookieParser());
app.set("view engine", "ejs");
app.set("views", path.join(__dirname, "views"));
app.use(express.static(path.join(__dirname, "public")));

app.use(async (req, res, next) => {
    const token = req.cookies.token;
    if (token) {
        try {
            const decoded = jwt.verify(token, process.env.JWT_SECRET || 'your-secret-key-change-in-production');
            const user = await User.findById(decoded.userId);
            if (user) {
                res.locals.user = {
                    id: user._id,
                    name: user.name,
                    role: user.role
                };
            } else if (decoded.role === 'admin') {
                res.locals.user = {
                    id: 'adminId',
                    name: 'alishaikhh15@gmail.com',
                    role: 'admin'
                };
            }
        } catch (error) {
            res.clearCookie('token');
        }
    }
    
    next();
});

mongoose.connect("mongodb://localhost:27017/farmrent")
  .then(() => console.log("MongoDB connected"))
  .catch(err => console.error(err));

app.use(bodyParser.urlencoded({ extended: true }));

app.get("/", (req, res) => {
    res.render("home");
});

// Direct soil-ai route handlers (guaranteed to work)
app.get("/soil-ai", async (req, res) => {
    console.log("✓ GET /soil-ai route accessed");
    try {
        const lang = req.query.lang || 'en';
        
        // Import agricultural config constants
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
        
        const { MAHARASHTRA_DISTRICTS, SOIL_TYPES, SEASONS, IRRIGATION_TYPES } = AGRICULTURAL_CONFIG;
        
        res.render("soil-ai", { 
            lang, 
            districts: MAHARASHTRA_DISTRICTS, 
            soilTypes: SOIL_TYPES, 
            seasons: SEASONS, 
            irrigationTypes: IRRIGATION_TYPES 
        });
    } catch (error) {
        console.error("Error rendering soil-ai:", error);
        res.status(500).send("Error: " + error.message);
    }
});

app.use("/auth", authRoutes);
app.use("/farmer", require("./routes/farmer"));
app.use("/seller", require("./routes/seller"));
app.use("/admin", require("./routes/admin"));

// Register soil-ai router for POST /soil-ai/analyze only
try {
    const soilAiRoutes = require("./routes/soil_ai");
    app.use("/soil-ai", soilAiRoutes);
    console.log("✓ Soil AI router registered for POST /soil-ai/analyze");
} catch (error) {
    console.error("✗ Error loading soil-ai route:", error);
}

app.use("/api/translation", require("./routes/translation"));

app.listen(3000, () => console.log("Server running on http://localhost:3000"));
