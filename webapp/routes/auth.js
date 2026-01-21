const express = require("express");
const router = express.Router();
const User = require("../models/User");
const bcrypt = require("bcrypt");
const { generateToken } = require("../middleware/auth");

// Signup (only for farmer & seller)
router.get("/signup/:role", (req, res) => {
    const role = req.params.role;
    if (role === "admin") return res.send("Admin signup not allowed");
    res.render("signup", { role, error: null });
});

router.post("/signup/:role", async (req, res) => {
    const { name, email, password } = req.body;
    const role = req.params.role;
    if (!name || !email || !password) return res.send("All fields required");

    try {
        // Check if email already exists
        const existingUser = await User.findOne({ email });
        if (existingUser) {
            return res.render("signup", { 
                role, 
                error: "Email already registered. Please use a different email or login." 
            });
        }

        const user = new User({ name, email, password, role });
        await user.save();
        res.redirect(`/auth/login/${role}`);
    } catch (err) {
        console.error(err);
        // Handle duplicate key error if it somehow gets past the check
        if (err.code === 11000) {
            return res.render("signup", { 
                role, 
                error: "Email already registered. Please use a different email or login." 
            });
        }
        res.render("signup", { role, error: "Error creating account. Please try again." });
    }
});

// Login
router.get("/login/:role", (req, res) => {
    res.render("login", { role: req.params.role, error: null });
});

router.post("/login/:role", async (req, res) => {
    const { email, password } = req.body;
    const role = req.params.role;

    try {
        let user;

        if (role === "admin") {
            if (email === "alishaikhh15@gmail.com" && password === "123") {
                const token = generateToken("adminId", "admin");
                console.log('[auth] issuing admin token');
                // Clear any old token set with a different domain/path
                res.clearCookie('token', { path: '/' });
                res.cookie('token', token, {
                    httpOnly: true,
                    // Use secure=false for local HTTP; set to true only behind HTTPS
                    secure: false,
                    sameSite: 'lax',
                    path: '/',
                    maxAge: 24 * 60 * 60 * 1000
                });
                // Also set a cookie scoped to 127.0.0.1 for users who access via IP
                res.cookie('token', token, {
                    httpOnly: true,
                    secure: false,
                    sameSite: 'lax',
                    path: '/',
                    domain: '127.0.0.1',
                    maxAge: 24 * 60 * 60 * 1000
                });
                return res.redirect("/admin");
            } else {
                return res.render("login", { role, error: "Invalid admin credentials" });
            }
        } else {
            user = await User.findOne({ email, role });
            if (!user) {
                return res.render("login", { role, error: "User not found" });
            }
            
            const match = await bcrypt.compare(password, user.password);
            if (!match) {
                return res.render("login", { role, error: "Invalid credentials" });
            }

            const token = generateToken(user._id.toString(), user.role);
            console.log('[auth] issuing user token for role', user.role);
            // Clear any old token set with a different domain/path
            res.clearCookie('token', { path: '/' });
            res.cookie('token', token, {
                httpOnly: true,
                // Use secure=false for local HTTP; set to true only behind HTTPS
                secure: false,
                sameSite: 'lax',
                path: '/',
                maxAge: 24 * 60 * 60 * 1000
            });
            // Also set a cookie scoped to 127.0.0.1 for users who access via IP
            res.cookie('token', token, {
                httpOnly: true,
                secure: false,
                sameSite: 'lax',
                path: '/',
                domain: '127.0.0.1',
                maxAge: 24 * 60 * 60 * 1000
            });

            if (role === "farmer") return res.redirect("/farmer");
            if (role === "seller") return res.redirect("/seller");
        }

    } catch (err) {
        console.error(err);
        res.render("login", { role, error: "Error logging in" });
    }
});

// Logout
router.get("/logout", (req, res) => {
    res.clearCookie('token');
    res.redirect("/");
});

module.exports = router;
