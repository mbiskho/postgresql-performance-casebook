-- Support console: an agent types part of a customer's name.
SELECT id, full_name, email, country, signup_at
FROM customers
WHERE full_name ILIKE {{pattern}}
ORDER BY signup_at DESC
LIMIT 25;
