-- Valentine Cookbook schema. SQLite, one file, easy to back up by copying it.

CREATE TABLE IF NOT EXISTS users (
    id           INTEGER PRIMARY KEY,
    email        TEXT    NOT NULL UNIQUE,
    full_name    TEXT    NOT NULL,
    created_at   TEXT    NOT NULL,
    last_seen_at TEXT,
    is_blocked   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS login_tokens (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT    NOT NULL UNIQUE,
    created_at TEXT    NOT NULL,
    expires_at TEXT    NOT NULL,
    used_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_tokens_user ON login_tokens(user_id);

CREATE TABLE IF NOT EXISTS recipes (
    id              INTEGER PRIMARY KEY,
    slug            TEXT    NOT NULL UNIQUE,
    title           TEXT    NOT NULL,
    description     TEXT    NOT NULL DEFAULT '',
    cuisine         TEXT    NOT NULL DEFAULT '',
    meal            TEXT    NOT NULL DEFAULT '',
    servings        REAL,
    servings_label  TEXT    NOT NULL DEFAULT 'servings',
    prep_minutes    INTEGER,
    cook_minutes    INTEGER,
    steps           TEXT    NOT NULL DEFAULT '',
    notes           TEXT    NOT NULL DEFAULT '',
    source          TEXT    NOT NULL DEFAULT '',
    -- Who typed it in (an account) versus whose recipe it actually is (just a
    -- name). Grandma never had an email address, but the recipe is hers.
    submitted_by_id   INTEGER REFERENCES users(id),
    submitted_by_name TEXT  NOT NULL,
    credited_to       TEXT  NOT NULL DEFAULT '',
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL,
    is_hidden       INTEGER NOT NULL DEFAULT 0,
    search_blob     TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_recipes_cuisine ON recipes(cuisine);
CREATE INDEX IF NOT EXISTS idx_recipes_meal    ON recipes(meal);
CREATE INDEX IF NOT EXISTS idx_recipes_submitter ON recipes(submitted_by_id);

CREATE TABLE IF NOT EXISTS ingredients (
    id         INTEGER PRIMARY KEY,
    recipe_id  INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    position   INTEGER NOT NULL,
    raw        TEXT    NOT NULL,
    qty        REAL,
    qty_max    REAL,
    unit       TEXT    NOT NULL DEFAULT '',
    item       TEXT    NOT NULL DEFAULT '',
    is_heading INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_ingredients_recipe ON ingredients(recipe_id, position);

CREATE TABLE IF NOT EXISTS tags (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS recipe_tags (
    recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    tag_id    INTEGER NOT NULL REFERENCES tags(id)    ON DELETE CASCADE,
    PRIMARY KEY (recipe_id, tag_id)
);

-- Photo files live on disk next to the database; this table is just the index.
CREATE TABLE IF NOT EXISTS photos (
    id         INTEGER PRIMARY KEY,
    recipe_id  INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    filename   TEXT    NOT NULL,
    position   INTEGER NOT NULL DEFAULT 0,
    width      INTEGER,
    height     INTEGER,
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_photos_recipe ON photos(recipe_id, position);

CREATE TABLE IF NOT EXISTS comments (
    id          INTEGER PRIMARY KEY,
    recipe_id   INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    user_id     INTEGER REFERENCES users(id),
    author_name TEXT    NOT NULL,
    body        TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    is_hidden   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_comments_recipe ON comments(recipe_id, created_at);

-- Simple sliding-window rate limiting; rows older than a day get swept.
CREATE TABLE IF NOT EXISTS rate_events (
    id         INTEGER PRIMARY KEY,
    bucket     TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rate_bucket ON rate_events(bucket, created_at);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
