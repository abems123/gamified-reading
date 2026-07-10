CREATE TABLE IF NOT EXISTS users(id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, 
                username VARCHAR(255) UNIQUE NOT NULL, 
                full_name VARCHAR(255), 
                email VARCHAR(255) UNIQUE NOT NULL,
                password_hash VARCHAR(255),
                level INT DEFAULT 0,
                coins INT DEFAULT 100,
                total_xp INT DEFAULT 0,
                todays_xp INT DEFAULT 0,
                uploaded_books INT DEFAULT 0);

CREATE TABLE IF NOT EXISTS books(id INT PRIMARY KEY, 
                user_id INT REFERENCES users(id),
                pages INT,
                read_pages INT DEFAULT 0,
                start_page INT DEFAULT 0,
                minutes_spent INT DEFAULT 0);