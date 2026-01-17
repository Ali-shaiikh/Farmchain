"""
Centralized Agricultural Configuration
This file contains all predefined agricultural rules, thresholds, and domain-specific constants.
Single source of truth for soil parameters, crop-season mappings, and validation rules.
"""

# ============================================================================
# SOIL PARAMETER THRESHOLDS (kg/ha)
# ============================================================================
SOIL_THRESHOLDS = {
    "Nitrogen": {
        "Low": (0, 199),          # < 200
        "Medium": (200, 280),     # 200-280
        "High": (281, 10000),     # > 280
    },
    "Phosphorus": {
        "Low": (0, 9),            # < 10
        "Medium": (10, 25),       # 10-25
        "High": (26, 10000),      # > 25
    },
    "Potassium": {
        "Low": (0, 109),          # < 110
        "Medium": (110, 280),     # 110-280
        "High": (281, 10000),     # > 280
    },
    "Organic Carbon": {           # Percentage (%)
        "Poor": (0, 0.39),        # < 0.4
        "Medium": (0.4, 0.75),    # 0.4-0.75
        "Rich": (0.76, 100),      # > 0.75
    }
}

# pH Categorization (Unitless)
PH_THRESHOLDS = {
    "Acidic": (0, 6.4),          # < 6.5
    "Neutral": (6.5, 7.5),       # 6.5-7.5
    "Alkaline": (7.51, 14),      # > 7.5
}

# ============================================================================
# CROP-SEASON MAPPINGS (Maharashtra, India)
# ============================================================================
KHARIF_CROPS = [
    "Soybean", "Tur", "Cotton", "Maize", "Rice", 
    "Bajra", "Jowar", "Groundnut", "Sugarcane"
]

RABI_CROPS = [
    "Wheat", "Gram", "Onion", "Tomato", "Potato", 
    "Mustard", "Sunflower", "Garlic", "Fenugreek", "Coriander"
]

SUMMER_CROPS = [
    "Watermelon", "Muskmelon", "Cucumber", "Bitter Gourd", "Okra"
]

# Map season names to crop lists
SEASON_CROPS = {
    "Kharif": KHARIF_CROPS,
    "Rabi": RABI_CROPS,
    "Summer": SUMMER_CROPS,
}

# ============================================================================
# CROP FERTILITY REQUIREMENTS
# ============================================================================
HIGH_INPUT_CROPS = [
    "Onion", "Sugarcane", "Banana", "Cotton"
]

CROP_FERTILITY_RULES = {
    "high_input": {
        "crops": HIGH_INPUT_CROPS,
        "nitrogen_threshold": "Low",        # Filter if N <= Low
        "organic_carbon_threshold": "Poor", # Filter if OC <= Poor
        "reason": "Requires good soil fertility"
    }
}

# ============================================================================
# ADMINISTRATIVE DIVISIONS
# ============================================================================
MAHARASHTRA_DISTRICTS = [
    "Thane", "Pune", "Nashik", "Aurangabad", "Nagpur", "Kolhapur",
    "Satara", "Solapur", "Sangli", "Ahmednagar", "Jalgaon", "Dhule",
    "Nanded", "Latur", "Osmanabad", "Beed", "Jalna", "Parbhani",
    "Hingoli", "Washim", "Buldhana", "Akola", "Amravati", "Yavatmal",
    "Wardha", "Chandrapur", "Gadchiroli", "Bhandara", "Gondia", "Raigad",
    "Ratnagiri", "Sindhudurg"
]

SOIL_TYPES = [
    "Loamy", "Clayey", "Sandy", "Alluvial", "Black", "Red", "Laterite"
]

SEASONS = ["Kharif", "Rabi", "Summer"]

IRRIGATION_TYPES = ["Rain-fed", "Irrigated"]

# ============================================================================
# HELPER FUNCTIONS FOR CATEGORIZATION
# ============================================================================

def categorize_parameter(param_name: str, value: float) -> str:
    """
    Categorize a soil parameter based on its numeric value.
    
    Args:
        param_name: Parameter name (e.g., "Nitrogen", "pH", "Organic Carbon")
        value: Numeric value to categorize
    
    Returns:
        Category string (e.g., "Low", "Medium", "High", "Acidic", "Neutral")
    
    Raises:
        ValueError: If parameter not recognized or value out of range
    """
    if param_name == "pH":
        return categorize_ph(value)
    
    if param_name in SOIL_THRESHOLDS:
        thresholds = SOIL_THRESHOLDS[param_name]
        for category, (min_val, max_val) in thresholds.items():
            if min_val <= value <= max_val:
                return category
        raise ValueError(f"Value {value} out of range for {param_name}")
    
    raise ValueError(f"Unknown parameter: {param_name}")


def categorize_ph(value: float) -> str:
    """
    Categorize pH value.
    
    Args:
        value: pH value (typically 0-14)
    
    Returns:
        Category: "Acidic", "Neutral", or "Alkaline"
    
    Raises:
        ValueError: If pH value outside valid range
    """
    if not isinstance(value, (int, float)) or value < 0 or value > 14:
        raise ValueError(f"Invalid pH value: {value}. pH must be between 0 and 14.")
    
    if value < 6.5:
        return "Acidic"
    elif value <= 7.5:
        return "Neutral"
    else:
        return "Alkaline"


def get_crop_list_for_season(season: str) -> list:
    """
    Get crop list for a specific season.
    
    Args:
        season: Season name ("Kharif", "Rabi", or "Summer")
    
    Returns:
        List of crops for the season
    
    Raises:
        ValueError: If season not recognized
    """
    if season not in SEASON_CROPS:
        raise ValueError(f"Unknown season: {season}. Must be one of {list(SEASON_CROPS.keys())}")
    return SEASON_CROPS[season]


def is_crop_in_season(crop: str, season: str) -> bool:
    """
    Check if a crop belongs to a specific season.
    
    Args:
        crop: Crop name
        season: Season name
    
    Returns:
        True if crop is in season, False otherwise
    """
    try:
        crop_list = get_crop_list_for_season(season)
        return crop.lower() in [c.lower() for c in crop_list]
    except ValueError:
        return False


def validate_crops_for_season(crops: list, season: str) -> bool:
    """
    Validate that all crops belong to a specific season.
    
    Args:
        crops: List of crop names
        season: Season name
    
    Returns:
        True if all crops are valid for season, False otherwise
    """
    if not crops or not season:
        return False
    
    return all(is_crop_in_season(crop, season) for crop in crops)


def should_filter_crop(crop: str, nitrogen_category: str, organic_carbon_category: str) -> bool:
    """
    Determine if a crop should be filtered based on soil fertility.
    
    Args:
        crop: Crop name
        nitrogen_category: Nitrogen category ("Low", "Medium", "High")
        organic_carbon_category: Organic carbon category ("Poor", "Medium", "Rich")
    
    Returns:
        True if crop should be filtered out, False otherwise
    """
    rules = CROP_FERTILITY_RULES.get("high_input", {})
    high_input_crops = rules.get("crops", [])
    
    if crop not in high_input_crops:
        return False
    
    # Filter if nitrogen is Low or organic carbon is Poor
    if nitrogen_category == "Low" or organic_carbon_category == "Poor":
        return True
    
    return False


# ============================================================================
# DISCLAIMERS
# ============================================================================

DISCLAIMER_EN = (
    "This recommendation is based on soil reports, district conditions, and "
    "standard agriculture guidelines. Please consult your local agriculture "
    "officer for final decisions."
)

DISCLAIMER_MR = (
    "हा सल्ला माती अहवाल, जिल्ह्याची परिस्थिती व मानक कृषी मार्गदर्शक तत्वांवर आधारित आहे. "
    "अंतिम निर्णयासाठी स्थानिक कृषी अधिकाऱ्यांचा सल्ला घ्यावा."
)

DISCLAIMERS = {
    "english": DISCLAIMER_EN,
    "marathi": DISCLAIMER_MR,
    "en": DISCLAIMER_EN,
    "mr": DISCLAIMER_MR,
}


def get_disclaimer(language: str = "english") -> str:
    """
    Get disclaimer in specified language.
    
    Args:
        language: Language code ("english", "marathi", "en", "mr")
    
    Returns:
        Disclaimer string
    """
    return DISCLAIMERS.get(language.lower(), DISCLAIMER_EN)
