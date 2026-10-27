CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, api_key TEXT, disabled_at DATETIME, type TEXT NOT NULL DEFAULT 'login');
CREATE TABLE logins (id INTEGER PRIMARY KEY, email TEXT NOT NULL, display_email TEXT NOT NULL, user_id INTEGER NOT NULL);
CREATE INDEX logins_email ON logins (email);
