DO $$ 
BEGIN 
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'membership_type') THEN 
        CREATE TYPE membership_type AS ENUM ('free', 'plus', 'pro');
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS users(id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, 
                username VARCHAR(255) UNIQUE NOT NULL, 
                full_name VARCHAR(255), 
                email VARCHAR(255) UNIQUE NOT NULL,
                password_hash VARCHAR(255),
                membership membership_type,
                level INT DEFAULT 0,
                coins INT DEFAULT 100,
                total_xp INT DEFAULT 0,
                todays_xp INT DEFAULT 0,
                uploaded_books INT DEFAULT 0);

CREATE TABLE IF NOT EXISTS streaks(
    id INT REFERENCES users(id) ON DELETE CASCADE PRIMARY KEY, 
    days INT DEFAULT 0, 
    streak_freeze_available BOOLEAN DEFAULT FALSE);

CREATE TABLE IF NOT EXISTS books(
    id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id INT REFERENCES users(id) ON DELETE CASCADE,
    title VARCHAR(255) NOT NULL,
    filename VARCHAR(255) NOT NULL,
    pages INT,
    read_pages INT DEFAULT 0,
    start_page INT DEFAULT 0,
    seconds_today INT DEFAULT 0,
    seconds_spent INT DEFAULT 0,
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_opened_at TIMESTAMP,
    last_read_date DATE
);

CREATE TABLE IF NOT EXISTS quests(
    id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id INT REFERENCES users(id) ON DELETE CASCADE,
    description VARCHAR(255) NOT NULL,
    goal_type VARCHAR(50),
    goal_amount INT,
    progress INT DEFAULT 0,
    xp_reward INT DEFAULT 0,
    coin_reward INT DEFAULT 0,
    completed BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS leaderboard(
    user_id INT REFERENCES users(id) ON DELETE CASCADE PRIMARY KEY,
    score INT DEFAULT 0
);