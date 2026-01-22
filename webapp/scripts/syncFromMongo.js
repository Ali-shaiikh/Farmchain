const hre = require("hardhat");
const mongoose = require("mongoose");
const Listing = require("../models/Listing");

async function main() {
  await mongoose.connect("mongodb://localhost:27017/farmrent");

  const listings = await Listing.find({});
  console.log("Found", listings.length, "machineries in Mongo");

  const FarmMachinery = await hre.ethers.getContractFactory("FarmMachinery");
  const contract = FarmMachinery.attach("0x5FbDB2315678afecb367f032d93F642f64180aa3");

  for (const item of listings) {
    const tx = await contract.listMachinery(
      item.name,
      hre.ethers.utils.parseEther("0.01"), // TEMP price
      hre.ethers.utils.parseEther("0.03")
    );
    const receipt = await tx.wait();

    console.log("✅ Listed on-chain:", item.name);
  }

  process.exit(0);
}

main().catch(console.error);
