CREATE DATABASE postgres;
CREATE TABLE IF NOT EXISTS reports (
            id SERIAL PRIMARY KEY,
            agency_id INTEGER REFERENCES agencies(id),
            message TEXT NOT NULL,
            image_path VARCHAR(500),
            latitude DECIMAL(10, 8),
            longitude DECIMAL(11, 8),
            location_method VARCHAR(50),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)

CREATE TABLE IF NOT EXISTS agencies (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)
