const express = require("express");
const mongoose = require("mongoose");
const path = require("path");
const bodyParser = require("body-parser");
const cookieParser = require("cookie-parser");
const jwt = require("jsonwebtoken");
const cors = require("cors");

const User = require("./models/User");
const authRoutes = require("./routes/auth");

const app = express();

/* =========================
   ✅ GLOBAL MIDDLEWARE
========================= */

// CORS (must be first)
app.use(
  cors({
    origin: "http://localhost:3000",
    credentials: true
  })
);

app.use(cookieParser());
app.use(express.json());
app.use(bodyParser.urlencoded({ extended: true }));

/* Disable caching for dynamic pages */
app.disable("etag");
app.use((req, res, next) => {
  res.set("Cache-Control", "no-store, no-cache, must-revalidate, private");
  next();
});

/* Simple request logger */
app.use((req, res, next) => {
  const start = Date.now();
  res.on("finish", () => {
    console.log(
      `[${req.method}] ${req.originalUrl} -> ${res.statusCode} (${Date.now() - start}ms)`
    );
  });
  next();
});

/* =========================
   ✅ VIEW ENGINE
========================= */

app.set("view engine", "ejs");
app.set("views", path.join(__dirname, "views"));
app.use(express.static(path.join(__dirname, "public")));

/* =========================
   ✅ AUTH TOKEN MIDDLEWARE
========================= */

app.use(async (req, res, next) => {
  const token = req.cookies.token;

  if (token) {
    try {
      const decoded = jwt.verify(
        token,
        process.env.JWT_SECRET || "your-secret-key-change-in-production"
      );

      if (decoded.role === "admin") {
        req.user = { userId: "adminId", role: "admin", name: "Admin" };
        res.locals.user = req.user;
      } else {
        const user = await User.findById(decoded.userId);
        if (user) {
          req.user = {
            userId: user._id,
            role: user.role,
            name: user.name
          };
          res.locals.user = req.user;
        }
      }
    } catch (err) {
      console.log("JWT error:", err.message);
      res.clearCookie("token");
    }
  }

  next();
});

/* =========================
   ✅ MONGODB
========================= */

mongoose
  .connect("mongodb://localhost:27017/farmrent", {
    useNewUrlParser: true,
    useUnifiedTopology: true
  })
  .then(() => console.log("MongoDB connected"))
  .catch(err => console.error(err));

/* =========================
   ✅ HOME
========================= */

app.get("/", (req, res) => {
  res.render("home");
});

/* =========================
   🌱 SOIL AI – PAGE ROUTE
========================= */

app.get("/soil-ai", async (req, res) => {
  console.log("✓ GET /soil-ai");

  try {
    const lang = req.query.lang || "en";

    const AGRICULTURAL_CONFIG = {
      MAHARASHTRA_DISTRICTS: [
        "Thane","Pune","Nashik","Aurangabad","Nagpur","Kolhapur",
        "Satara","Solapur","Sangli","Ahmednagar","Jalgaon","Dhule",
        "Nanded","Latur","Osmanabad","Beed","Jalna","Parbhani",
        "Hingoli","Washim","Buldhana","Akola","Amravati","Yavatmal",
        "Wardha","Chandrapur","Gadchiroli","Bhandara","Gondia",
        "Raigad","Ratnagiri","Sindhudurg"
      ],
      SOIL_TYPES: ["Loamy", "Clayey", "Sandy", "Alluvial", "Black", "Red", "Laterite"],
      SEASONS: ["Kharif", "Rabi", "Summer"],
      IRRIGATION_TYPES: ["Rain-fed", "Irrigated"]
    };

    res.render("soil-ai", {
      lang,
      districts: AGRICULTURAL_CONFIG.MAHARASHTRA_DISTRICTS,
      soilTypes: AGRICULTURAL_CONFIG.SOIL_TYPES,
      seasons: AGRICULTURAL_CONFIG.SEASONS,
      irrigationTypes: AGRICULTURAL_CONFIG.IRRIGATION_TYPES
    });

  } catch (err) {
    console.error("Soil AI render error:", err);
    res.status(500).send("Error loading Soil AI");
  }
});

/* =========================
   🌱 SOIL AI – API ROUTES
   (POST /soil-ai/analyze)
========================= */

try {
  const soilAiRoutes = require("./routes/soil_ai");
  app.use("/soil-ai/api", soilAiRoutes);
  console.log("✓ Soil AI router mounted");
} catch (err) {
  console.error("✗ Soil AI router load failed:", err.message);
}

/* =========================
   ✅ OTHER ROUTES
========================= */

app.use("/auth", authRoutes);
app.use("/farmer", require("./routes/farmer"));
app.use("/seller", require("./routes/seller"));
app.use("/admin", require("./routes/admin"));
app.use("/analytics", require("./routes/analytics"));
app.use("/api/translation", require("./routes/translation"));

/* =========================
   ✅ START SERVER
========================= */

app.listen(3000, () => {
  console.log("🚀 Server running on http://localhost:3000");
});
