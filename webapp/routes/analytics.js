const express = require("express");
const path = require("path");
const fs = require("fs");
const ejs = require("ejs");
const router = express.Router();
const Booking = require("../models/Booking");
const Listing = require("../models/Listing");
const { verifyToken } = require("../middleware/auth");

const viewsDir = path.join(__dirname, "../views");

/**
 * FARMER ANALYTICS
 */
router.get("/farmer", verifyToken, async (req, res) => {
  try {
    const bookings = await Booking.find({ farmer: req.user.userId, status: "confirmed" })
      .populate("listing");

    let totalSpent = 0;
    let totalDays = 0;
    const usageMap = {};

    bookings.forEach(b => {
      totalSpent += b.amount || 0;
      const days = Math.round(((b.to || b.from) ? (b.to - b.from) : 0) / 86400000);
      totalDays += days;

      const name = b.listing?.name || "Unknown";
      usageMap[name] = (usageMap[name] || 0) + days;
    });

    res.set("Cache-Control", "no-store, no-cache, must-revalidate, private");
    res.set("Pragma", "no-cache");
    res.set("Expires", "0");
    res.set("Surrogate-Control", "no-store");
    res.set("ETag", `${Date.now()}`);

    const viewPath = path.join(viewsDir, "analytics-farmer.ejs");
    if (!fs.existsSync(viewPath)) {
      console.error("Farmer analytics view missing at", viewPath);
      return res.status(500).send("Analytics template missing");
    }

    console.log("Rendering farmer analytics from", viewPath, "size", fs.statSync(viewPath).size);

    const html = await ejs.renderFile(viewPath, {
      totalSpent,
      totalDays,
      usageMap,
      user: res.locals.user
    });

    console.log("Farmer analytics HTML length:", html?.length || 0);
    if (!html) {
      console.error("Farmer analytics rendered empty HTML");
      return res.status(500).send("Analytics render returned empty");
    }
    return res.status(200).send(html);
  } catch (err) {
    console.error("Error loading farmer analytics:", err);
    return res.status(500).send("Error loading analytics");
  }
});

/**
 * SELLER ANALYTICS
 */
router.get("/seller", verifyToken, async (req, res) => {
  try {
    const listings = await Listing.find({ owner: req.user.userId });
    const listingIds = listings.map(l => l._id);

    const bookings = await Booking.find({ listing: { $in: listingIds }, status: "confirmed" })
      .populate("listing");

    let totalIncome = 0;
    const machineRevenue = {};

    bookings.forEach(b => {
      totalIncome += b.amount || 0;
      const name = b.listing?.name || "Unknown";
      machineRevenue[name] = (machineRevenue[name] || 0) + (b.amount || 0);
    });

    res.set("Cache-Control", "no-store, no-cache, must-revalidate, private");
    res.set("Pragma", "no-cache");
    res.set("Expires", "0");
    res.set("Surrogate-Control", "no-store");
    res.set("ETag", `${Date.now()}`);

    const viewPath = path.join(viewsDir, "analytics-seller.ejs");
    if (!fs.existsSync(viewPath)) {
      console.error("Seller analytics view missing at", viewPath);
      return res.status(500).send("Analytics template missing");
    }

    console.log("Rendering seller analytics from", viewPath, "size", fs.statSync(viewPath).size);

    const html = await ejs.renderFile(viewPath, {
      totalIncome,
      machineRevenue,
      user: res.locals.user
    });

    console.log("Seller analytics HTML length:", html?.length || 0);
    if (!html) {
      console.error("Seller analytics rendered empty HTML");
      return res.status(500).send("Analytics render returned empty");
    }
    return res.status(200).send(html);
  } catch (err) {
    console.error("Error loading seller analytics:", err);
    return res.status(500).send("Error loading analytics");
  }
});

module.exports = router;