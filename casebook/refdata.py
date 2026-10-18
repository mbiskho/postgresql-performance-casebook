"""Reference data for the seeder.

Weights are relative. They are turned into 1000-slot lookup tables so a uniform
random number picks a value with the right frequency in O(1) inside SQL.

The point of this data is the *shape*, not the specific values: skewed country
and city mixes, columns that are functionally dependent on each other (country
-> currency, locale; city -> timezone), and Zipf-like popularity for products,
customers and pages. Uniform data produces plans nobody sees in production.
"""

from __future__ import annotations

# country, weight, currency, locale, [(city, weight, timezone)]
COUNTRIES: list[tuple[str, int, str, str, list[tuple[str, int, str]]]] = [
    ("ID", 300, "IDR", "id-ID", [
        ("Jakarta", 30, "Asia/Jakarta"), ("Surabaya", 12, "Asia/Jakarta"),
        ("Bandung", 10, "Asia/Jakarta"), ("Medan", 8, "Asia/Jakarta"),
        ("Tangerang", 6, "Asia/Jakarta"), ("Semarang", 6, "Asia/Jakarta"),
        ("Makassar", 6, "Asia/Makassar"), ("Denpasar", 5, "Asia/Makassar"),
        ("Yogyakarta", 5, "Asia/Jakarta"), ("Palembang", 4, "Asia/Jakarta"),
        ("Malang", 4, "Asia/Jakarta"), ("Balikpapan", 3, "Asia/Makassar"),
        ("Jayapura", 1, "Asia/Jayapura"),
    ]),
    ("US", 170, "USD", "en-US", [
        ("New York", 18, "America/New_York"), ("Los Angeles", 14, "America/Los_Angeles"),
        ("Chicago", 10, "America/Chicago"), ("Houston", 9, "America/Chicago"),
        ("Phoenix", 6, "America/Phoenix"), ("Seattle", 6, "America/Los_Angeles"),
        ("Miami", 6, "America/New_York"), ("Denver", 5, "America/Denver"),
        ("Boston", 5, "America/New_York"), ("Austin", 5, "America/Chicago"),
        ("Atlanta", 5, "America/New_York"), ("Portland", 3, "America/Los_Angeles"),
    ]),
    ("IN", 110, "INR", "en-IN", [
        ("Mumbai", 20, "Asia/Kolkata"), ("Delhi", 20, "Asia/Kolkata"),
        ("Bengaluru", 16, "Asia/Kolkata"), ("Hyderabad", 10, "Asia/Kolkata"),
        ("Chennai", 10, "Asia/Kolkata"), ("Pune", 8, "Asia/Kolkata"),
        ("Kolkata", 8, "Asia/Kolkata"), ("Ahmedabad", 6, "Asia/Kolkata"),
    ]),
    ("BR", 80, "BRL", "pt-BR", [
        ("Sao Paulo", 30, "America/Sao_Paulo"), ("Rio de Janeiro", 18, "America/Sao_Paulo"),
        ("Belo Horizonte", 8, "America/Sao_Paulo"), ("Brasilia", 8, "America/Sao_Paulo"),
        ("Salvador", 6, "America/Bahia"), ("Fortaleza", 6, "America/Fortaleza"),
        ("Manaus", 4, "America/Manaus"), ("Curitiba", 6, "America/Sao_Paulo"),
    ]),
    ("MX", 50, "MXN", "es-MX", [
        ("Mexico City", 40, "America/Mexico_City"), ("Guadalajara", 15, "America/Mexico_City"),
        ("Monterrey", 15, "America/Monterrey"), ("Puebla", 8, "America/Mexico_City"),
        ("Tijuana", 7, "America/Tijuana"), ("Cancun", 5, "America/Cancun"),
    ]),
    ("DE", 50, "EUR", "de-DE", [
        ("Berlin", 25, "Europe/Berlin"), ("Hamburg", 14, "Europe/Berlin"),
        ("Munich", 14, "Europe/Berlin"), ("Cologne", 9, "Europe/Berlin"),
        ("Frankfurt", 8, "Europe/Berlin"), ("Stuttgart", 6, "Europe/Berlin"),
    ]),
    ("GB", 50, "GBP", "en-GB", [
        ("London", 45, "Europe/London"), ("Manchester", 12, "Europe/London"),
        ("Birmingham", 12, "Europe/London"), ("Leeds", 7, "Europe/London"),
        ("Glasgow", 7, "Europe/London"), ("Bristol", 6, "Europe/London"),
    ]),
    ("FR", 40, "EUR", "fr-FR", [
        ("Paris", 40, "Europe/Paris"), ("Lyon", 12, "Europe/Paris"),
        ("Marseille", 12, "Europe/Paris"), ("Toulouse", 8, "Europe/Paris"),
        ("Nice", 6, "Europe/Paris"), ("Bordeaux", 6, "Europe/Paris"),
    ]),
    ("JP", 40, "JPY", "ja-JP", [
        ("Tokyo", 45, "Asia/Tokyo"), ("Osaka", 18, "Asia/Tokyo"),
        ("Yokohama", 10, "Asia/Tokyo"), ("Nagoya", 9, "Asia/Tokyo"),
        ("Fukuoka", 7, "Asia/Tokyo"), ("Sapporo", 6, "Asia/Tokyo"),
    ]),
    ("PH", 30, "PHP", "en-PH", [
        ("Manila", 35, "Asia/Manila"), ("Quezon City", 25, "Asia/Manila"),
        ("Cebu City", 15, "Asia/Manila"), ("Davao City", 12, "Asia/Manila"),
    ]),
    ("AU", 30, "AUD", "en-AU", [
        ("Sydney", 35, "Australia/Sydney"), ("Melbourne", 32, "Australia/Melbourne"),
        ("Brisbane", 14, "Australia/Brisbane"), ("Perth", 10, "Australia/Perth"),
        ("Adelaide", 6, "Australia/Adelaide"),
    ]),
    ("NL", 30, "EUR", "nl-NL", [
        ("Amsterdam", 40, "Europe/Amsterdam"), ("Rotterdam", 25, "Europe/Amsterdam"),
        ("The Hague", 18, "Europe/Amsterdam"), ("Utrecht", 15, "Europe/Amsterdam"),
    ]),
    ("SG", 20, "SGD", "en-SG", [
        ("Singapore", 1, "Asia/Singapore"),
    ]),
]

FIRST_NAMES = [
    "Adi", "Agus", "Ahmad", "Aisyah", "Alex", "Amelia", "Ana", "Andi", "Anna",
    "Arjun", "Ayu", "Benjamin", "Bima", "Budi", "Camila", "Carlos", "Charlotte",
    "Chen", "Citra", "Daniel", "David", "Dewi", "Diego", "Dimas", "Elena",
    "Emily", "Emma", "Fajar", "Fatima", "Gabriel", "Gita", "Hana", "Hans",
    "Hiroshi", "Indra", "Intan", "Isabella", "James", "Jose", "Juan", "Julia",
    "Kartika", "Kevin", "Laura", "Lucas", "Lukas", "Maria", "Mateo", "Maya",
    "Michael", "Mia", "Muhammad", "Nadia", "Nina", "Noah", "Nur", "Olivia",
    "Paul", "Priya", "Putri", "Rahul", "Rani", "Rina", "Rizky", "Ryo",
    "Sakura", "Santiago", "Sara", "Sari", "Siti", "Sofia", "Sophie", "Tari",
    "Thomas", "Tri", "Wahyu", "Wei", "William", "Yuki", "Yusuf",
]

LAST_NAMES = [
    "Anderson", "Becker", "Brown", "Chen", "Costa", "Dubois", "Fernandes",
    "Fischer", "Garcia", "Gonzalez", "Gupta", "Hakim", "Halim", "Harahap",
    "Hartono", "Hernandez", "Hidayat", "Hoffmann", "Ito", "Johnson", "Jones",
    "Kato", "Khan", "Kim", "Kobayashi", "Kumar", "Kurniawan", "Kusuma",
    "Kusumawardani", "Lee", "Lim", "Lopez", "Martin", "Martinez", "Meyer",
    "Miller", "Moreau", "Muller", "Nakamura", "Nasution", "Nguyen", "Oliveira",
    "Pereira", "Permana", "Pratama", "Putra", "Rahman", "Reyes", "Rodrigues",
    "Rodriguez", "Saputra", "Santos", "Sato", "Schmidt", "Schneider", "Setiawan",
    "Sharma", "Silva", "Simanjuntak", "Singh", "Siregar", "Smith", "Suzuki",
    "Tan", "Tanaka", "Taylor", "Thomas", "Wang", "Watanabe", "Wibowo",
    "Wijaya", "Williams", "Wilson", "Wong", "Yamamoto", "Yang", "Yusuf",
]

EMAIL_DOMAINS = [
    ("gmail.com", 52), ("yahoo.com", 12), ("outlook.com", 10), ("hotmail.com", 7),
    ("icloud.com", 6), ("proton.me", 2), ("mail.example.co", 11),
]

TIERS = [("standard", 85), ("plus", 12), ("business", 3)]

CATEGORIES = [
    ("Electronics", 18), ("Fashion", 16), ("Home & Kitchen", 12), ("Beauty", 10),
    ("Grocery", 9), ("Sports", 6), ("Toys", 5), ("Books", 5), ("Automotive", 4),
    ("Health", 4), ("Office", 3), ("Pet Supplies", 3), ("Garden", 2),
    ("Music", 1), ("Baby", 2),
]

BRANDS = [
    "Acme", "Northwind", "Contoso", "Globex", "Initech", "Umbrella", "Stark",
    "Wayne", "Hooli", "Vandelay", "Soylent", "Tyrell", "Cyberdyne", "Aperture",
    "Wonka", "Gringotts", "Monarch", "Oscorp", "Massive", "Pied Piper",
    "Prestige", "Sirius", "Virtucon", "Zorg", "Nakatomi", "Duff", "Krusty",
    "Bluth", "Dunder", "Sterling",
]

ADJECTIVES = [
    "Classic", "Pro", "Ultra", "Smart", "Eco", "Compact", "Deluxe", "Essential",
    "Premium", "Lite", "Max", "Mini", "Travel", "Studio", "Active", "Everyday",
]

NOUNS = [
    "Headphones", "Blender", "Jacket", "Sneakers", "Serum", "Backpack", "Lamp",
    "Kettle", "Mouse", "Keyboard", "Bottle", "Notebook", "Speaker", "Watch",
    "Charger", "Pan", "Towel", "Shampoo", "Mat", "Chair",
]

REFERRERS = [
    (None, 40), ("https://www.google.com/", 30), ("https://www.instagram.com/", 9),
    ("https://www.facebook.com/", 7), ("https://t.co/", 4), ("https://www.bing.com/", 3),
    ("https://mail.example.co/", 4), ("https://www.youtube.com/", 3),
]


def page_paths() -> list[str]:
    """Most popular first; the seeder picks with a Zipf-like rank."""
    head = ["/", "/search", "/cart", "/checkout", "/account/orders", "/deals", "/login"]
    cats = ["/c/" + c.lower().replace(" & ", "-").replace(" ", "-") for c, _ in CATEGORIES]
    prods = [f"/p/{i}" for i in range(1, 281)]
    return head + cats + prods


def slots(weighted: list[tuple[object, int]], n: int = 1000) -> list[object]:
    """Expand weights into exactly n slots using largest-remainder rounding."""
    total = sum(w for _, w in weighted)
    exact = [(v, w * n / total) for v, w in weighted]
    counts = [int(e) for _, e in exact]
    remainder = n - sum(counts)
    order = sorted(range(len(exact)), key=lambda i: exact[i][1] - counts[i], reverse=True)
    for i in order[:remainder]:
        counts[i] += 1
    out: list[object] = []
    for (v, _), c in zip(exact, counts):
        out.extend([v] * c)
    return out
