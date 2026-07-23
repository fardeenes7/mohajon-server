from __future__ import annotations

import random
from decimal import Decimal
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.text import slugify

from shops.models import Shop, StockLocation
from catalog.models import Category, Product, ProductVariant, ProductStatus, StockRecord


REAL_DATASET = [
    # ── Category: Electronics & Gadgets ──────────────────────────────────────────
    {
        "category": "Electronics & Gadgets",
        "products": [
            {
                "name": "Wireless Noise Cancelling Headphones WH-1000XM5",
                "description": "Premium over-ear wireless headphones featuring industry-leading noise canceling, 30-hour battery life, crisp hands-free calling, and ultra-comfortable lightweight design.",
                "base_price": Decimal("349.99"),
                "compare_at_price": Decimal("399.99"),
                "purchase_price": Decimal("210.00"),
                "is_digital": False,
                "weight_grams": 250,
                "specifications": {"Brand": "Sony", "Connectivity": "Bluetooth 5.2", "Battery Life": "30 hrs", "Noise Canceling": "Active"},
                "variants": [
                    {"attr1_name": "Color", "attr1_val": "Black", "sku": "SONY-WH5-BLK", "price_override": None, "stock": 45},
                    {"attr1_name": "Color", "attr1_val": "Silver", "sku": "SONY-WH5-SLV", "price_override": None, "stock": 30},
                    {"attr1_name": "Color", "attr1_val": "Midnight Blue", "sku": "SONY-WH5-BLU", "price_override": Decimal("369.99"), "stock": 15},
                ],
            },
            {
                "name": "Mechanical Gaming Keyboard RGB Backlit",
                "description": "Tactile mechanical gaming keyboard with hot-swappable switches, customizable per-key RGB lighting, durable aluminum top plate, and detachable USB-C cable.",
                "base_price": Decimal("89.99"),
                "compare_at_price": Decimal("119.99"),
                "purchase_price": Decimal("45.00"),
                "is_digital": False,
                "weight_grams": 950,
                "specifications": {"Switch Type": "Mechanical", "Layout": "Tenkeyless (80%)", "Backlight": "RGB"},
                "variants": [
                    {"attr1_name": "Switch Type", "attr1_val": "Red Linear", "sku": "KB-RGB-RED", "price_override": None, "stock": 60},
                    {"attr1_name": "Switch Type", "attr1_val": "Blue Clicky", "sku": "KB-RGB-BLU", "price_override": None, "stock": 40},
                    {"attr1_name": "Switch Type", "attr1_val": "Brown Tactile", "sku": "KB-RGB-BRN", "price_override": None, "stock": 35},
                ],
            },
            {
                "name": "Ergonomic Wireless Vertical Mouse",
                "description": "Scientific ergonomic design encourages neutral wrist and arm positions for smoother movement and less overall strain. 2.4G wireless and Bluetooth multi-device connection.",
                "base_price": Decimal("39.99"),
                "compare_at_price": Decimal("49.99"),
                "purchase_price": Decimal("18.00"),
                "is_digital": False,
                "weight_grams": 140,
                "specifications": {"DPI": "4000 max", "Connectivity": "Bluetooth + 2.4GHz", "Handedness": "Right-handed"},
                "variants": [],  # No variants
                "stock_single": 80,
            },
            {
                "name": "Ultra-Wide Curved Gaming Monitor 34-inch",
                "description": "34-inch WQHD (3440 x 1440) 1500R curved monitor with 165Hz refresh rate, 1ms response time, HDR10 support, and AMD FreeSync Premium for immersive gameplay.",
                "base_price": Decimal("499.00"),
                "compare_at_price": Decimal("599.00"),
                "purchase_price": Decimal("320.00"),
                "is_digital": False,
                "weight_grams": 7800,
                "specifications": {"Screen Size": "34 inch", "Resolution": "3440x1440", "Refresh Rate": "165Hz", "Panel Type": "VA"},
                "variants": [],
                "stock_single": 25,
            },
            {
                "name": "Smart Watch Series 8 GPS + Cellular",
                "description": "Advanced health tracking smartwatch featuring ECG monitor, Blood Oxygen sensor, Always-On Retina display, crash detection, and fitness tracking water resistant up to 50m.",
                "base_price": Decimal("399.00"),
                "compare_at_price": Decimal("449.00"),
                "purchase_price": Decimal("260.00"),
                "is_digital": False,
                "weight_grams": 50,
                "specifications": {"Display": "Always-On OLED", "Water Resistance": "50m", "Connectivity": "GPS + Cellular"},
                "variants": [
                    {"attr1_name": "Case Size", "attr1_val": "41mm", "attr2_name": "Band Color", "attr2_val": "Midnight", "sku": "SW-8-41-MID", "price_override": None, "stock": 20},
                    {"attr1_name": "Case Size", "attr1_val": "41mm", "attr2_name": "Band Color", "attr2_val": "Starlight", "sku": "SW-8-41-STL", "price_override": None, "stock": 18},
                    {"attr1_name": "Case Size", "attr1_val": "45mm", "attr2_name": "Band Color", "attr2_val": "Midnight", "sku": "SW-8-45-MID", "price_override": Decimal("429.00"), "stock": 25},
                    {"attr1_name": "Case Size", "attr1_val": "45mm", "attr2_name": "Band Color", "attr2_val": "Silver", "sku": "SW-8-45-SLV", "price_override": Decimal("429.00"), "stock": 12},
                ],
            },
            {
                "name": "Portable Power Bank 20,000mAh 65W Fast Charging",
                "description": "High-capacity portable charger capable of powering laptops, tablets, and smartphones. Dual USB-C PD ports and digital LED power level display.",
                "base_price": Decimal("59.99"),
                "compare_at_price": Decimal("79.99"),
                "purchase_price": Decimal("28.00"),
                "is_digital": False,
                "weight_grams": 450,
                "specifications": {"Capacity": "20,000 mAh", "Max Output": "65W USB-C PD"},
                "variants": [],
                "stock_single": 110,
            },
            {
                "name": "4K Ultra HD Streaming Media Player Stick",
                "description": "Cinematic 4K streaming player supporting Dolby Vision, HDR10+, and immersive Dolby Atmos audio. Comes with voice remote control.",
                "base_price": Decimal("49.99"),
                "compare_at_price": Decimal("59.99"),
                "purchase_price": Decimal("22.00"),
                "is_digital": False,
                "weight_grams": 120,
                "specifications": {"Resolution": "4K Ultra HD", "Audio": "Dolby Atmos"},
                "variants": [],
                "stock_single": 150,
            },
            {
                "name": "USB-C Multiport Adapter 8-in-1 Hub",
                "description": "Aluminum USB-C hub featuring 4K HDMI, 100W Power Delivery pass-through, SD/TF card readers, 3x USB 3.0 ports, and Gigabit Ethernet.",
                "base_price": Decimal("34.99"),
                "compare_at_price": Decimal("44.99"),
                "purchase_price": Decimal("14.00"),
                "is_digital": False,
                "weight_grams": 85,
                "specifications": {"Ports": "HDMI 4K, 100W PD, Ethernet, SD/MicroSD, 3x USB 3.0"},
                "variants": [
                    {"attr1_name": "Finish", "attr1_val": "Space Gray", "sku": "HUB-8IN1-GRY", "price_override": None, "stock": 90},
                    {"attr1_name": "Finish", "attr1_val": "Silver", "sku": "HUB-8IN1-SLV", "price_override": None, "stock": 70},
                ],
            },
        ],
    },

    # ── Category: Men's Fashion ──────────────────────────────────────────────────
    {
        "category": "Men's Fashion",
        "products": [
            {
                "name": "Classic Fit 100% Cotton Oxford Button-Down Shirt",
                "description": "Timeless long-sleeve oxford shirt crafted from breathable premium cotton. Features chest pocket, button-down collar, and durable stitching for everyday wear.",
                "base_price": Decimal("45.00"),
                "compare_at_price": Decimal("55.00"),
                "purchase_price": Decimal("18.00"),
                "is_digital": False,
                "weight_grams": 350,
                "specifications": {"Material": "100% Cotton Oxford", "Fit": "Classic Fit", "Care": "Machine Washable"},
                "variants": [
                    {"attr1_name": "Size", "attr1_val": "Small", "attr2_name": "Color", "attr2_val": "White", "sku": "M-SHIRT-S-WHT", "price_override": None, "stock": 25},
                    {"attr1_name": "Size", "attr1_val": "Medium", "attr2_name": "Color", "attr2_val": "White", "sku": "M-SHIRT-M-WHT", "price_override": None, "stock": 40},
                    {"attr1_name": "Size", "attr1_val": "Large", "attr2_name": "Color", "attr2_val": "White", "sku": "M-SHIRT-L-WHT", "price_override": None, "stock": 30},
                    {"attr1_name": "Size", "attr1_val": "Medium", "attr2_name": "Color", "attr2_val": "Light Blue", "sku": "M-SHIRT-M-BLU", "price_override": None, "stock": 35},
                    {"attr1_name": "Size", "attr1_val": "Large", "attr2_name": "Color", "attr2_val": "Light Blue", "sku": "M-SHIRT-L-BLU", "price_override": None, "stock": 28},
                ],
            },
            {
                "name": "Slim Fit Chino Pants Flex Stretch",
                "description": "Versatile men's chino pants with slight stretch performance fabric for all-day comfort. Flat front style ideal for casual or smart business wear.",
                "base_price": Decimal("54.99"),
                "compare_at_price": Decimal("64.99"),
                "purchase_price": Decimal("22.00"),
                "is_digital": False,
                "weight_grams": 480,
                "specifications": {"Material": "98% Cotton, 2% Elastane", "Fit": "Slim Fit"},
                "variants": [
                    {"attr1_name": "Size", "attr1_val": "30x32", "attr2_name": "Color", "attr2_val": "Khaki", "sku": "CHINO-3032-KHK", "price_override": None, "stock": 20},
                    {"attr1_name": "Size", "attr1_val": "32x32", "attr2_name": "Color", "attr2_val": "Khaki", "sku": "CHINO-3232-KHK", "price_override": None, "stock": 45},
                    {"attr1_name": "Size", "attr1_val": "34x32", "attr2_name": "Color", "attr2_val": "Khaki", "sku": "CHINO-3432-KHK", "price_override": None, "stock": 30},
                    {"attr1_name": "Size", "attr1_val": "32x32", "attr2_name": "Color", "attr2_val": "Navy", "sku": "CHINO-3232-NVY", "price_override": None, "stock": 35},
                    {"attr1_name": "Size", "attr1_val": "34x32", "attr2_name": "Color", "attr2_val": "Navy", "sku": "CHINO-3432-NVY", "price_override": None, "stock": 25},
                ],
            },
            {
                "name": "Men's Waterproof Breathable Rain Jacket Hooded",
                "description": "Lightweight windproof and rainproof shell with fully sealed seams, adjustable drawstring hood, zippered pockets, and packable design.",
                "base_price": Decimal("79.99"),
                "compare_at_price": Decimal("99.99"),
                "purchase_price": Decimal("35.00"),
                "is_digital": False,
                "weight_grams": 410,
                "specifications": {"Waterproof Rating": "10,000mm", "Material": "100% Polyester Ripstop"},
                "variants": [
                    {"attr1_name": "Size", "attr1_val": "Medium", "attr2_name": "Color", "attr2_val": "Black", "sku": "M-JKT-M-BLK", "price_override": None, "stock": 18},
                    {"attr1_name": "Size", "attr1_val": "Large", "attr2_name": "Color", "attr2_val": "Black", "sku": "M-JKT-L-BLK", "price_override": None, "stock": 22},
                    {"attr1_name": "Size", "attr1_val": "Large", "attr2_name": "Color", "attr2_val": "Forest Green", "sku": "M-JKT-L-GRN", "price_override": None, "stock": 14},
                ],
            },
            {
                "name": "Men's Genuine Leather Belt Dress Casual",
                "description": "100% full grain genuine leather belt with classic single-prong alloy buckle. Ideal for pairing with dress pants or jeans.",
                "base_price": Decimal("29.99"),
                "compare_at_price": Decimal("39.99"),
                "purchase_price": Decimal("10.00"),
                "is_digital": False,
                "weight_grams": 180,
                "specifications": {"Material": "100% Full Grain Leather", "Width": "1.4 inches"},
                "variants": [
                    {"attr1_name": "Size", "attr1_val": "34", "attr2_name": "Color", "attr2_val": "Brown", "sku": "BELT-34-BRN", "price_override": None, "stock": 30},
                    {"attr1_name": "Size", "attr1_val": "36", "attr2_name": "Color", "attr2_val": "Brown", "sku": "BELT-36-BRN", "price_override": None, "stock": 25},
                    {"attr1_name": "Size", "attr1_val": "34", "attr2_name": "Color", "attr2_val": "Black", "sku": "BELT-34-BLK", "price_override": None, "stock": 40},
                ],
            },
            {
                "name": "Men's Lightweight Running Shoes Athletic Sneakers",
                "description": "Breathable mesh upper with cushioned EVA midsole for responsive shock absorption during road running, gym training, or daily walking.",
                "base_price": Decimal("69.99"),
                "compare_at_price": Decimal("84.99"),
                "purchase_price": Decimal("30.00"),
                "is_digital": False,
                "weight_grams": 620,
                "specifications": {"Upper Material": "Engineered Mesh", "Sole": "Non-slip Rubber"},
                "variants": [
                    {"attr1_name": "Size", "attr1_val": "US 9", "attr2_name": "Color", "attr2_val": "Black/White", "sku": "SHOE-M9-BW", "price_override": None, "stock": 15},
                    {"attr1_name": "Size", "attr1_val": "US 10", "attr2_name": "Color", "attr2_val": "Black/White", "sku": "SHOE-M10-BW", "price_override": None, "stock": 20},
                    {"attr1_name": "Size", "attr1_val": "US 11", "attr2_name": "Color", "attr2_val": "Grey", "sku": "SHOE-M11-GRY", "price_override": None, "stock": 12},
                ],
            },
            {
                "name": "Heavyweight Fleece Pullover Hoodie",
                "description": "Soft plush fleece interior with kangaroo pocket, double-lined hood, and ribbed cuffs. Premium streetwear comfort.",
                "base_price": Decimal("49.99"),
                "compare_at_price": Decimal("59.99"),
                "purchase_price": Decimal("20.00"),
                "is_digital": False,
                "weight_grams": 650,
                "specifications": {"Material": "80% Cotton, 20% Polyester", "Weight": "330 GSM"},
                "variants": [
                    {"attr1_name": "Size", "attr1_val": "Medium", "attr2_name": "Color", "attr2_val": "Charcoal", "sku": "HD-M-CHA", "price_override": None, "stock": 30},
                    {"attr1_name": "Size", "attr1_val": "Large", "attr2_name": "Color", "attr2_val": "Charcoal", "sku": "HD-L-CHA", "price_override": None, "stock": 35},
                    {"attr1_name": "Size", "attr1_val": "Large", "attr2_name": "Color", "attr2_val": "Olive", "sku": "HD-L-OLV", "price_override": None, "stock": 20},
                ],
            },
        ],
    },

    # ── Category: Women's Fashion & Beauty ─────────────────────────────────────
    {
        "category": "Women's Fashion & Beauty",
        "products": [
            {
                "name": "High-Waisted Yoga Pants Leggings with Pockets",
                "description": "Butter-soft 4-way stretch fabric leggings with tummy control high waistband and deep side pockets for phone and essentials.",
                "base_price": Decimal("32.99"),
                "compare_at_price": Decimal("42.99"),
                "purchase_price": Decimal("12.00"),
                "is_digital": False,
                "weight_grams": 240,
                "specifications": {"Material": "75% Nylon, 25% Spandex", "Length": "7/8 Ankle Length"},
                "variants": [
                    {"attr1_name": "Size", "attr1_val": "Small", "attr2_name": "Color", "attr2_val": "Black", "sku": "LEG-S-BLK", "price_override": None, "stock": 50},
                    {"attr1_name": "Size", "attr1_val": "Medium", "attr2_name": "Color", "attr2_val": "Black", "sku": "LEG-M-BLK", "price_override": None, "stock": 65},
                    {"attr1_name": "Size", "attr1_val": "Large", "attr2_name": "Color", "attr2_val": "Black", "sku": "LEG-L-BLK", "price_override": None, "stock": 40},
                    {"attr1_name": "Size", "attr1_val": "Medium", "attr2_name": "Color", "attr2_val": "Plum", "sku": "LEG-M-PLM", "price_override": None, "stock": 30},
                ],
            },
            {
                "name": "Floral Print Boho Maxi Dress",
                "description": "Flowy bohemian summer maxi dress with V-neckline, adjustable waist drawstring, and lightweight breathable fabric.",
                "base_price": Decimal("48.50"),
                "compare_at_price": Decimal("59.99"),
                "purchase_price": Decimal("21.00"),
                "is_digital": False,
                "weight_grams": 320,
                "specifications": {"Material": "100% Rayon", "Neckline": "V-Neck"},
                "variants": [
                    {"attr1_name": "Size", "attr1_val": "Small", "attr2_name": "Color", "attr2_val": "Yellow Floral", "sku": "MAXI-S-YEL", "price_override": None, "stock": 18},
                    {"attr1_name": "Size", "attr1_val": "Medium", "attr2_name": "Color", "attr2_val": "Yellow Floral", "sku": "MAXI-M-YEL", "price_override": None, "stock": 22},
                    {"attr1_name": "Size", "attr1_val": "Medium", "attr2_name": "Color", "attr2_val": "Navy Floral", "sku": "MAXI-M-NVY", "price_override": None, "stock": 15},
                ],
            },
            {
                "name": "Hyaluronic Acid Hydrating Face Serum 30ml",
                "description": "Pure Hyaluronic Acid serum with Vitamin B5 for intense facial hydration, plumping fine lines, and restoring natural skin radiance.",
                "base_price": Decimal("24.99"),
                "compare_at_price": Decimal("29.99"),
                "purchase_price": Decimal("7.50"),
                "is_digital": False,
                "weight_grams": 110,
                "specifications": {"Volume": "30ml / 1 fl oz", "Skin Type": "All Skin Types", "Key Ingredient": "Hyaluronic Acid 2% + B5"},
                "variants": [],
                "stock_single": 120,
            },
            {
                "name": "Organic Rosewater Facial Toner Spray 100ml",
                "description": "100% pure steam-distilled Moroccan rose water. Alcohol-free hydrating mist for face and hair to soothe and rebalance skin pH.",
                "base_price": Decimal("16.99"),
                "compare_at_price": Decimal("21.99"),
                "purchase_price": Decimal("5.00"),
                "is_digital": False,
                "weight_grams": 160,
                "specifications": {"Volume": "100ml", "Ingredients": "100% Rosa Damascena Flower Water"},
                "variants": [],
                "stock_single": 85,
            },
            {
                "name": "Professional 1-Inch Ceramic Flat Iron Hair Straightener",
                "description": "Tourmaline ceramic floating plates distribute heat evenly to eliminate frizz and create smooth, silky straight hair or beachy waves.",
                "base_price": Decimal("42.00"),
                "compare_at_price": Decimal("55.00"),
                "purchase_price": Decimal("19.00"),
                "is_digital": False,
                "weight_grams": 490,
                "specifications": {"Plate Width": "1 Inch", "Max Temp": "450°F / 230°C", "Auto Shut-off": "60 mins"},
                "variants": [],
                "stock_single": 45,
            },
            {
                "name": "Matte Liquid Lipstick Long Lasting Waterproof",
                "description": "Velvet matte finish liquid lipstick with 16-hour stay power. Non-drying formula enriched with Vitamin E.",
                "base_price": Decimal("14.99"),
                "compare_at_price": Decimal("18.99"),
                "purchase_price": Decimal("3.80"),
                "is_digital": False,
                "weight_grams": 35,
                "specifications": {"Finish": "Matte", "Volume": "5ml"},
                "variants": [
                    {"attr1_name": "Shade", "attr1_val": "Ruby Red", "sku": "LIP-RUBY", "price_override": None, "stock": 40},
                    {"attr1_name": "Shade", "attr1_val": "Nude Pink", "sku": "LIP-NUDE", "price_override": None, "stock": 60},
                    {"attr1_name": "Shade", "attr1_val": "Berry Mauve", "sku": "LIP-BERRY", "price_override": None, "stock": 35},
                ],
            },
            {
                "name": "Leather Crossbody Shoulder Bag Vintage Tote",
                "description": "Soft synthetic vegan leather tote with magnetic snap closure, adjustable shoulder strap, and multiple internal zipper compartments.",
                "base_price": Decimal("39.99"),
                "compare_at_price": Decimal("49.99"),
                "purchase_price": Decimal("16.00"),
                "is_digital": False,
                "weight_grams": 550,
                "specifications": {"Material": "PU Vegan Leather", "Dimensions": "11 x 8.5 x 4 inches"},
                "variants": [
                    {"attr1_name": "Color", "attr1_val": "Cognac Brown", "sku": "BAG-COG", "price_override": None, "stock": 25},
                    {"attr1_name": "Color", "attr1_val": "Classic Black", "sku": "BAG-BLK", "price_override": None, "stock": 30},
                ],
            },
        ],
    },

    # ── Category: Home & Kitchen Appliances ────────────────────────────────────
    {
        "category": "Home & Kitchen",
        "products": [
            {
                "name": "Digital Air Fryer Oven 5.8 Quart XL",
                "description": "8-in-1 preset touchscreen air fryer. Uses 85% less oil than deep frying while maintaining crispy fried texture. Dishwasher-safe nonstick basket.",
                "base_price": Decimal("99.99"),
                "compare_at_price": Decimal("129.99"),
                "purchase_price": Decimal("52.00"),
                "is_digital": False,
                "weight_grams": 5400,
                "specifications": {"Capacity": "5.8 QT", "Power": "1700W", "Presets": "8 Quick Touch"},
                "variants": [],
                "stock_single": 40,
            },
            {
                "name": "Stainless Steel Electric Gooseneck Kettle 1.0L",
                "description": "Precision pouring kettle with 5 temperature presets for pour-over coffee and tea. 1200W rapid heating and keep-warm setting.",
                "base_price": Decimal("59.99"),
                "compare_at_price": Decimal("69.99"),
                "purchase_price": Decimal("28.00"),
                "is_digital": False,
                "weight_grams": 1250,
                "specifications": {"Capacity": "1.0 Liter", "Material": "304 Stainless Steel", "Power": "1200W"},
                "variants": [
                    {"attr1_name": "Finish", "attr1_val": "Matte Black", "sku": "KET-BLK", "price_override": None, "stock": 35},
                    {"attr1_name": "Finish", "attr1_val": "Brushed Steel", "sku": "KET-STL", "price_override": None, "stock": 25},
                ],
            },
            {
                "name": "High-Speed Countertop Blender 1200W",
                "description": "Professional power blender with 64 oz BPA-free pitcher. Blends smoothies, hot soups, frozen desserts, and crushed ice effortlessly.",
                "base_price": Decimal("119.00"),
                "compare_at_price": Decimal("149.00"),
                "purchase_price": Decimal("65.00"),
                "is_digital": False,
                "weight_grams": 4200,
                "specifications": {"Power": "1200 Watts", "Capacity": "64 oz Pitcher", "Blades": "6-Leaf Stainless Steel"},
                "variants": [],
                "stock_single": 30,
            },
            {
                "name": "Robot Vacuum Cleaner with Self-Charging 2000Pa",
                "description": "Ultra-thin smart robot vacuum featuring automatic docking, obstacle sensors, quiet operation, and boundary strip support for hardwood and low carpets.",
                "base_price": Decimal("179.99"),
                "compare_at_price": Decimal("229.99"),
                "purchase_price": Decimal("95.00"),
                "is_digital": False,
                "weight_grams": 3100,
                "specifications": {"Suction Power": "2000Pa", "Runtime": "100 minutes", "Noise Level": "< 55dB"},
                "variants": [],
                "stock_single": 20,
            },
            {
                "name": "Ergonomic Memory Foam Contour Pillow",
                "description": "Cervical pillow for neck pain relief. Orthopedic contour support design for back, side, and stomach sleepers.",
                "base_price": Decimal("35.99"),
                "compare_at_price": Decimal("45.99"),
                "purchase_price": Decimal("14.00"),
                "is_digital": False,
                "weight_grams": 1300,
                "specifications": {"Fill": "100% Memory Foam", "Cover": "Washable Bamboo Viscose"},
                "variants": [
                    {"attr1_name": "Size", "attr1_val": "Standard", "sku": "PIL-STD", "price_override": None, "stock": 50},
                    {"attr1_name": "Size", "attr1_val": "Queen", "sku": "PIL-QEN", "price_override": Decimal("39.99"), "stock": 40},
                ],
            },
            {
                "name": "Chef Knife 8-Inch German High Carbon Steel",
                "description": "Ultra-sharp professional kitchen knife with ergonomic Pakkawood handle for precise chopping, slicing, and dicing.",
                "base_price": Decimal("39.99"),
                "compare_at_price": Decimal("54.99"),
                "purchase_price": Decimal("15.00"),
                "is_digital": False,
                "weight_grams": 280,
                "specifications": {"Blade Length": "8 Inches", "Steel": "German 5Cr15MoV Steel", "HRC": "56±2"},
                "variants": [],
                "stock_single": 65,
            },
            {
                "name": "Nonstick Ceramic Cookware Set 10-Piece",
                "description": "Eco-friendly nonstick pots and pans set free of PTFE/PFOA. Includes saucepans, stockpot, frying pans, and tempered glass lids.",
                "base_price": Decimal("149.99"),
                "compare_at_price": Decimal("189.99"),
                "purchase_price": Decimal("75.00"),
                "is_digital": False,
                "weight_grams": 8200,
                "specifications": {"Coating": "Ceramic Nonstick", "Pieces": "10-Piece Set", "Oven Safe": "Up to 450°F"},
                "variants": [
                    {"attr1_name": "Color", "attr1_val": "Cream White", "sku": "POT-CRM", "price_override": None, "stock": 15},
                    {"attr1_name": "Color", "attr1_val": "Sage Green", "sku": "POT-SGE", "price_override": None, "stock": 12},
                ],
            },
            {
                "name": "Cold Brew Iced Coffee Maker 1.5 Liter",
                "description": "Extra thick borosilicate glass pitcher with removable fine mesh stainless steel filter for smooth, low-acid cold brew coffee.",
                "base_price": Decimal("27.99"),
                "compare_at_price": Decimal("34.99"),
                "purchase_price": Decimal("11.00"),
                "is_digital": False,
                "weight_grams": 720,
                "specifications": {"Capacity": "1.5 Liters / 50 oz", "Filter": "304 Stainless Steel 100-Mesh"},
                "variants": [],
                "stock_single": 50,
            },
        ],
    },

    # ── Category: Sports, Outdoors & Fitness ────────────────────────────────────
    {
        "category": "Sports & Fitness",
        "products": [
            {
                "name": "Adjustable Dumbbell Set 5 to 52.5 lbs Single",
                "description": "Space-saving adjustable dumbbell with easy selection dial replacing 15 sets of weights. Durable molding prevents clanking.",
                "base_price": Decimal("199.00"),
                "compare_at_price": Decimal("249.00"),
                "purchase_price": Decimal("110.00"),
                "is_digital": False,
                "weight_grams": 24000,
                "specifications": {"Weight Range": "5 to 52.5 lbs", "Increments": "2.5 lb steps up to 25 lbs"},
                "variants": [],
                "stock_single": 30,
            },
            {
                "name": "Non-Slip Yoga Mat 6mm Thick TPE Eco-Friendly",
                "description": "High-density cushioning exercise mat with alignment lines. Dual-layer anti-tear texture for yoga, pilates, and floor workouts.",
                "base_price": Decimal("29.99"),
                "compare_at_price": Decimal("39.99"),
                "purchase_price": Decimal("11.50"),
                "is_digital": False,
                "weight_grams": 900,
                "specifications": {"Thickness": "6mm", "Material": "TPE Eco-Friendly", "Dimensions": "72 x 24 inches"},
                "variants": [
                    {"attr1_name": "Color", "attr1_val": "Teal/Blue", "sku": "YOGA-TEL", "price_override": None, "stock": 45},
                    {"attr1_name": "Color", "attr1_val": "Purple/Pink", "sku": "YOGA-PRP", "price_override": None, "stock": 40},
                    {"attr1_name": "Color", "attr1_val": "Dark Grey", "sku": "YOGA-GRY", "price_override": None, "stock": 35},
                ],
            },
            {
                "name": "Insulated Stainless Steel Water Bottle 32 oz",
                "description": "Double-wall vacuum insulated sports flask keeps drinks ice cold for 24 hours or hot for 12 hours. Includes straw lid and chug lid.",
                "base_price": Decimal("24.99"),
                "compare_at_price": Decimal("31.99"),
                "purchase_price": Decimal("9.00"),
                "is_digital": False,
                "weight_grams": 420,
                "specifications": {"Volume": "32 oz", "Insulation": "Double-wall Vacuum", "BPA Free": True},
                "variants": [
                    {"attr1_name": "Color", "attr1_val": "Matte Black", "sku": "BTL-32-BLK", "price_override": None, "stock": 75},
                    {"attr1_name": "Color", "attr1_val": "Ocean Blue", "sku": "BTL-32-BLU", "price_override": None, "stock": 60},
                    {"attr1_name": "Color", "attr1_val": "Olive Green", "sku": "BTL-32-OLV", "price_override": None, "stock": 50},
                ],
            },
            {
                "name": "Resistance Loop Exercise Bands Set of 5",
                "description": "Heavy duty latex resistance bands for strength training, physical therapy, home workouts, and stretching. Includes travel carry bag.",
                "base_price": Decimal("14.99"),
                "compare_at_price": Decimal("19.99"),
                "purchase_price": Decimal("4.20"),
                "is_digital": False,
                "weight_grams": 160,
                "specifications": {"Resistance Levels": "X-Light to X-Heavy (5 levels)", "Material": "100% Natural Latex"},
                "variants": [],
                "stock_single": 140,
            },
            {
                "name": "Camping Backpacking Tent 2-Person Waterproof",
                "description": "Easy setup lightweight dome tent with rainfly, mesh ventilation windows, and sturdy aluminum poles for 3-season outdoor camping.",
                "base_price": Decimal("89.99"),
                "compare_at_price": Decimal("119.99"),
                "purchase_price": Decimal("42.00"),
                "is_digital": False,
                "weight_grams": 2700,
                "specifications": {"Capacity": "2 Person", "Waterproof Index": "3000mm", "Poles": "7001 Aluminum"},
                "variants": [],
                "stock_single": 22,
            },
            {
                "name": "Speed Jump Rope with Ball Bearings",
                "description": "Tangle-free rapid speed jump rope with memory foam handles and adjustable steel wire cable for cardio, boxing, and fitness training.",
                "base_price": Decimal("11.99"),
                "compare_at_price": Decimal("15.99"),
                "purchase_price": Decimal("3.50"),
                "is_digital": False,
                "weight_grams": 180,
                "specifications": {"Cable Length": "10ft Adjustable", "Bearings": "360° Ball Bearing System"},
                "variants": [],
                "stock_single": 100,
            },
            {
                "name": "Trekking Poles Collapsible Hiking Sticks Pair",
                "description": "Lightweight aircraft-grade 7075 aluminum walking poles with quick flip lock and comfortable cork grip handles.",
                "base_price": Decimal("39.99"),
                "compare_at_price": Decimal("49.99"),
                "purchase_price": Decimal("16.50"),
                "is_digital": False,
                "weight_grams": 560,
                "specifications": {"Material": "7075 Aluminum", "Adjustable Length": "24 to 54 inches"},
                "variants": [],
                "stock_single": 40,
            },
        ],
    },

    # ── Category: Books, Stationery & Digital Software ──────────────────────────
    {
        "category": "Books & Software",
        "products": [
            {
                "name": "Atomic Habits by James Clear (Hardcover)",
                "description": "An Easy & Proven Way to Build Good Habits & Break Bad Ones. The million-copy bestseller providing a proven framework for improving every day.",
                "base_price": Decimal("27.00"),
                "compare_at_price": Decimal("32.00"),
                "purchase_price": Decimal("12.00"),
                "is_digital": False,
                "weight_grams": 450,
                "specifications": {"Author": "James Clear", "Format": "Hardcover", "Pages": "320"},
                "variants": [],
                "stock_single": 85,
            },
            {
                "name": "Designing Data-Intensive Applications (Paperback)",
                "description": "The definitive guide to the architecture of modern data systems. Key principles of reliability, scalability, and maintainability.",
                "base_price": Decimal("49.99"),
                "compare_at_price": Decimal("59.99"),
                "purchase_price": Decimal("25.00"),
                "is_digital": False,
                "weight_grams": 980,
                "specifications": {"Author": "Martin Kleppmann", "Format": "Paperback", "Publisher": "O'Reilly Media"},
                "variants": [],
                "stock_single": 60,
            },
            {
                "name": "Cloud Productivity Suite 1-Year Subscription License",
                "description": "Digital license key delivered instantly via email. Access premium cloud apps, 1TB cloud storage, and advanced security for 1 user.",
                "base_price": Decimal("69.99"),
                "compare_at_price": Decimal("79.99"),
                "purchase_price": Decimal("45.00"),
                "is_digital": True,
                "weight_grams": 0,
                "specifications": {"Delivery": "Instant Digital Download", "Duration": "12 Months", "Users": "1 User / 5 Devices"},
                "variants": [],
                "stock_single": 999,
            },
            {
                "name": "Premium Hardcover Dot Grid Journal Notebook",
                "description": "192 numbered pages of 120gsm thick bleedproof paper. Lay-flat binding with ribbon bookmark and back pocket.",
                "base_price": Decimal("18.99"),
                "compare_at_price": Decimal("22.99"),
                "purchase_price": Decimal("6.00"),
                "is_digital": False,
                "weight_grams": 380,
                "specifications": {"Paper Weight": "120 GSM", "Size": "A5 (5.7 x 8.2 inches)", "Page Count": "192"},
                "variants": [
                    {"attr1_name": "Cover Color", "attr1_val": "Emerald Green", "sku": "NOTE-EMR", "price_override": None, "stock": 40},
                    {"attr1_name": "Cover Color", "attr1_val": "Sapphire Blue", "sku": "NOTE-BLU", "price_override": None, "stock": 50},
                    {"attr1_name": "Cover Color", "attr1_val": "Mustard Yellow", "sku": "NOTE-YEL", "price_override": None, "stock": 30},
                ],
            },
            {
                "name": "Fineliner Color Pen Set 24 Pack",
                "description": "0.38mm extra fine tip colored sketch markers. Water-based ink, minimal bleed-through, ideal for bullet journaling and calendar planning.",
                "base_price": Decimal("12.99"),
                "compare_at_price": Decimal("16.99"),
                "purchase_price": Decimal("3.80"),
                "is_digital": False,
                "weight_grams": 190,
                "specifications": {"Tip Size": "0.38mm Fine Tip", "Count": "24 Assorted Colors"},
                "variants": [],
                "stock_single": 110,
            },
            {
                "name": "Antivirus & Internet Security 3-Device 1-Year Key",
                "description": "Comprehensive real-time malware protection, firewall, anti-phishing, and VPN for PC, Mac, and Mobile devices.",
                "base_price": Decimal("39.99"),
                "compare_at_price": Decimal("59.99"),
                "purchase_price": Decimal("15.00"),
                "is_digital": True,
                "weight_grams": 0,
                "specifications": {"Delivery": "Digital Code", "Devices": "3 Devices", "Duration": "1 Year"},
                "variants": [],
                "stock_single": 500,
            },
        ],
    },

    # ── Category: Health & Organic Food ─────────────────────────────────────────
    {
        "category": "Health & Gourmet Food",
        "products": [
            {
                "name": "100% Organic Raw Whey Protein Powder 2 lbs",
                "description": "Unflavored grass-fed whey protein isolate with zero artificial sweeteners or additives. 24g protein per serving.",
                "base_price": Decimal("44.99"),
                "compare_at_price": Decimal("54.99"),
                "purchase_price": Decimal("22.00"),
                "is_digital": False,
                "weight_grams": 907,
                "specifications": {"Weight": "2 lbs (907g)", "Protein per Serving": "24g", "Flavors": "Unflavored"},
                "variants": [],
                "stock_single": 55,
            },
            {
                "name": "Matcha Green Tea Powder Organic Ceremonial Grade 100g",
                "description": "First-harvest ceremonial grade Japanese matcha. Rich vibrant green powder packed with L-theanine antioxidants.",
                "base_price": Decimal("28.99"),
                "compare_at_price": Decimal("34.99"),
                "purchase_price": Decimal("11.00"),
                "is_digital": False,
                "weight_grams": 120,
                "specifications": {"Grade": "Ceremonial Grade", "Origin": "Uji, Kyoto, Japan", "Net Weight": "100g"},
                "variants": [],
                "stock_single": 70,
            },
            {
                "name": "Raw Organic Extra Virgin Coconut Oil 16 oz",
                "description": "Cold-pressed unrefined organic coconut oil. Ideal for baking, cooking, hair moisturizing, and natural skincare.",
                "base_price": Decimal("12.99"),
                "compare_at_price": Decimal("15.99"),
                "purchase_price": Decimal("4.50"),
                "is_digital": False,
                "weight_grams": 450,
                "specifications": {"Volume": "16 fl oz / 473ml", "Extraction": "Cold-Pressed Unrefined"},
                "variants": [],
                "stock_single": 90,
            },
            {
                "name": "Whole Bean Specialty Coffee Roast 12 oz Bag",
                "description": "Single-origin Arabica coffee beans ethically sourced. Notes of dark chocolate, toasted hazelnut, and caramel finish.",
                "base_price": Decimal("17.50"),
                "compare_at_price": Decimal("21.00"),
                "purchase_price": Decimal("6.80"),
                "is_digital": False,
                "weight_grams": 340,
                "specifications": {"Bean Type": "100% Arabica", "Weight": "12 oz (340g)"},
                "variants": [
                    {"attr1_name": "Roast Level", "attr1_val": "Medium Roast", "sku": "COF-MED", "price_override": None, "stock": 40},
                    {"attr1_name": "Roast Level", "attr1_val": "Dark Espresso Roast", "sku": "COF-DRK", "price_override": None, "stock": 45},
                    {"attr1_name": "Roast Level", "attr1_val": "Light Roast (Ethiopian)", "sku": "COF-LGT", "price_override": Decimal("18.50"), "stock": 30},
                ],
            },
            {
                "name": "Raw Manuka Honey MGO 400+ 250g",
                "description": "Authentic 100% pure New Zealand Manuka Honey. Superfood honey renowned for antibacterial immune support.",
                "base_price": Decimal("39.99"),
                "compare_at_price": Decimal("49.99"),
                "purchase_price": Decimal("19.00"),
                "is_digital": False,
                "weight_grams": 250,
                "specifications": {"Rating": "MGO 400+ (UMF 13+)", "Origin": "New Zealand", "Net Weight": "250g"},
                "variants": [],
                "stock_single": 35,
            },
            {
                "name": "Multivitamin Daily Dietary Supplement 120 Capsules",
                "description": "Complete daily multivitamin with Essential Vitamins A, C, D3, E, B-Complex, and Minerals for energy and immunity support.",
                "base_price": Decimal("21.99"),
                "compare_at_price": Decimal("26.99"),
                "purchase_price": Decimal("7.00"),
                "is_digital": False,
                "weight_grams": 150,
                "specifications": {"Count": "120 Veggie Caps", "Supply": "60 Days"},
                "variants": [],
                "stock_single": 100,
            },
            {
                "name": "Artisanal Dark Chocolate Bars 72% Cacao 4-Pack",
                "description": "Handcrafted single-origin Belgian dark chocolate bars infused with sea salt and roasted almond slivers.",
                "base_price": Decimal("15.99"),
                "compare_at_price": Decimal("19.99"),
                "purchase_price": Decimal("5.50"),
                "is_digital": False,
                "weight_grams": 360,
                "specifications": {"Cacao Content": "72%", "Quantity": "4 x 90g Bars"},
                "variants": [],
                "stock_single": 60,
            },
        ],
    },

    # ── Category: Toys, Baby & Gaming ───────────────────────────────────────────
    {
        "category": "Toys, Kids & Gaming",
        "products": [
            {
                "name": "Wireless Bluetooth Controller for PC & Console",
                "description": "Ergonomic game controller with dual vibration motors, 6-axis gyro sensor, turbo button, and 15-hour rechargeable battery.",
                "base_price": Decimal("39.99"),
                "compare_at_price": Decimal("49.99"),
                "purchase_price": Decimal("17.00"),
                "is_digital": False,
                "weight_grams": 280,
                "specifications": {"Compatibility": "PC, Switch, Android, iOS", "Battery": "600mAh"},
                "variants": [
                    {"attr1_name": "Color", "attr1_val": "Camouflage Green", "sku": "CTRL-CAMO", "price_override": None, "stock": 25},
                    {"attr1_name": "Color", "attr1_val": "Matte White", "sku": "CTRL-WHT", "price_override": None, "stock": 40},
                ],
            },
            {
                "name": "Building Blocks Space Shuttle Launch Set (850 Pcs)",
                "description": "Educational construction toy set with rocket shuttle, launch pad tower, and astronaut mini-figures.",
                "base_price": Decimal("59.99"),
                "compare_at_price": Decimal("74.99"),
                "purchase_price": Decimal("24.00"),
                "is_digital": False,
                "weight_grams": 1400,
                "specifications": {"Piece Count": "850 Pieces", "Recommended Age": "8+ Years"},
                "variants": [],
                "stock_single": 30,
            },
            {
                "name": "Soft Plush Teddy Bear 18-Inch",
                "description": "Classic ultra-soft stuffed teddy bear with satin bow collar. Huggable gift for kids and toddlers.",
                "base_price": Decimal("19.99"),
                "compare_at_price": Decimal("24.99"),
                "purchase_price": Decimal("6.50"),
                "is_digital": False,
                "weight_grams": 350,
                "specifications": {"Height": "18 Inches", "Material": "100% Polyester Plush"},
                "variants": [
                    {"attr1_name": "Color", "attr1_val": "Honey Brown", "sku": "BEAR-BRN", "price_override": None, "stock": 50},
                    {"attr1_name": "Color", "attr1_val": "Cream Pink", "sku": "BEAR-PNK", "price_override": None, "stock": 35},
                ],
            },
            {
                "name": "Ergonomic Baby Carrier 4-in-1 Front and Back",
                "description": "Adjustable infant carrier with lumbar support and breathable air mesh for babies from 8 to 33 lbs.",
                "base_price": Decimal("49.99"),
                "compare_at_price": Decimal("64.99"),
                "purchase_price": Decimal("20.00"),
                "is_digital": False,
                "weight_grams": 780,
                "specifications": {"Weight Capacity": "8 - 33 lbs", "Positions": "4 Carrying Styles"},
                "variants": [],
                "stock_single": 35,
            },
            {
                "name": "Strategy Board Game Fantasy Empire Quest",
                "description": "Award-winning tabletop strategy board game for 2 to 5 players. Includes miniature figures, custom dice, and modular map board.",
                "base_price": Decimal("45.00"),
                "compare_at_price": Decimal("55.00"),
                "purchase_price": Decimal("19.00"),
                "is_digital": False,
                "weight_grams": 1850,
                "specifications": {"Players": "2-5 Players", "Play Time": "60-90 Mins", "Age": "12+"},
                "variants": [],
                "stock_single": 40,
            },
        ],
    },
]


class Command(BaseCommand):
    help = (
        "Seeds categories, products, variants, and stock records into a specified shop "
        "for evaluation and RAG chatbot testing. Minimum 50 products guaranteed."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--shop",
            type=str,
            required=True,
            help="Shop slug or Shop UUID to populate with sample products.",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Soft-delete existing categories and products in the target shop before seeding.",
        )

    def handle(self, *args, **options) -> None:
        shop_identifier = options["shop"].strip()
        clear_existing = options["clear"]

        # Resolve Shop
        shop = None
        if len(shop_identifier) == 36 and "-" in shop_identifier:
            shop = Shop.objects.filter(id=shop_identifier).first()
        if not shop:
            shop = Shop.objects.filter(subdomain=shop_identifier).first()

        if not shop:
            raise CommandError(
                f"Shop '{shop_identifier}' not found. Please provide a valid shop subdomain, slug, or UUID."
            )

        self.stdout.write(
            self.style.NOTICE(f"Seeding test catalog data for Shop: '{shop.name}' (ID: {shop.id})")
        )

        default_location = StockLocation.objects.filter(shop=shop, is_default=True).first()
        if not default_location:
            # Fallback to any active location or create a default one
            default_location = StockLocation.objects.filter(shop=shop).first()
            if not default_location:
                default_location = StockLocation.objects.create(
                    shop=shop,
                    tenant_id=shop.id,
                    name="Main Store / Warehouse",
                    is_default=True,
                )
                self.stdout.write(self.style.SUCCESS(f"Created default StockLocation: {default_location.name}"))

        with transaction.atomic():
            if clear_existing:
                from django.utils import timezone
                now = timezone.now()
                deleted_prods = Product.objects.filter(shop=shop, deleted_at__isnull=True).update(deleted_at=now)
                deleted_cats = Category.objects.filter(shop=shop, deleted_at__isnull=True).update(deleted_at=now)
                self.stdout.write(
                    self.style.WARNING(f"Cleared existing catalog data: {deleted_prods} products, {deleted_cats} categories.")
                )

            total_products_created = 0
            total_variants_created = 0
            total_categories_created = 0

            for cat_sort, cat_data in enumerate(REAL_DATASET, start=1):
                cat_name = cat_data["category"]
                cat_slug = slugify(cat_name)
                
                category, cat_created = Category.objects.get_or_create(
                    shop=shop,
                    slug=cat_slug,
                    defaults={
                        "tenant_id": shop.id,
                        "name": cat_name,
                        "sort_order": cat_sort,
                        "is_active": True,
                    },
                )
                if cat_created:
                    total_categories_created += 1

                for prod_sort, p_data in enumerate(cat_data["products"], start=1):
                    prod_name = p_data["name"]
                    prod_slug = slugify(prod_name)

                    # Ensure unique product slug per shop if collision happens
                    base_slug = prod_slug
                    counter = 1
                    while Product.objects.filter(shop=shop, slug=prod_slug, deleted_at__isnull=True).exists():
                        prod_slug = f"{base_slug}-{counter}"
                        counter += 1

                    sku = p_data.get("sku", "")
                    if not sku:
                        sku = f"SKU-{cat_sort:02d}{prod_sort:02d}-{random.randint(100, 999)}"

                    product = Product.objects.create(
                        shop=shop,
                        tenant_id=shop.id,
                        category=category,
                        name=prod_name,
                        slug=prod_slug,
                        description=p_data["description"],
                        status=ProductStatus.PUBLISHED,
                        base_price=p_data["base_price"],
                        compare_at_price=p_data.get("compare_at_price"),
                        purchase_price=p_data.get("purchase_price", Decimal("0.00")),
                        sku=sku,
                        is_digital=p_data.get("is_digital", False),
                        weight_grams=p_data.get("weight_grams"),
                        specifications=p_data.get("specifications", {}),
                        sort_order=prod_sort,
                    )
                    total_products_created += 1

                    variants_list = p_data.get("variants", [])
                    if variants_list:
                        for v_idx, v_data in enumerate(variants_list, start=1):
                            v_sku = v_data["sku"]
                            variant = ProductVariant.objects.create(
                                product=product,
                                shop=shop,
                                tenant_id=shop.id,
                                attribute_name_1=v_data.get("attr1_name", ""),
                                attribute_value_1=v_data.get("attr1_val", ""),
                                attribute_name_2=v_data.get("attr2_name", ""),
                                attribute_value_2=v_data.get("attr2_val", ""),
                                sku=v_sku,
                                price_override=v_data.get("price_override"),
                                stock_quantity=v_data.get("stock", 20),
                                is_active=True,
                            )
                            total_variants_created += 1

                            StockRecord.objects.create(
                                shop=shop,
                                tenant_id=shop.id,
                                variant=variant,
                                location=default_location,
                                quantity=v_data.get("stock", 20),
                            )
                    else:
                        # Single variant representation for products without option variants
                        single_sku = f"{sku}-MAIN"
                        stock_qty = p_data.get("stock_single", 50)
                        variant = ProductVariant.objects.create(
                            product=product,
                            shop=shop,
                            tenant_id=shop.id,
                            attribute_name_1="",
                            attribute_value_1="",
                            sku=single_sku,
                            stock_quantity=stock_qty,
                            is_active=True,
                        )
                        total_variants_created += 1

                        StockRecord.objects.create(
                            shop=shop,
                            tenant_id=shop.id,
                            variant=variant,
                            location=default_location,
                            quantity=stock_qty,
                        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully seeded catalog for '{shop.name}':\n"
                f"  - Categories created: {total_categories_created}\n"
                f"  - Products created: {total_products_created}\n"
                f"  - Variants & StockRecords created: {total_variants_created}"
            )
        )
