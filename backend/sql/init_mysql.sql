CREATE DATABASE IF NOT EXISTS `yu_ai_learn`
CHARACTER SET utf8mb4
COLLATE utf8mb4_unicode_ci;

USE `yu_ai_learn`;

CREATE TABLE IF NOT EXISTS users (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    openid VARCHAR(64) NOT NULL,
    nickname VARCHAR(100) NOT NULL DEFAULT '学习者',
    avatar_url VARCHAR(500) NOT NULL DEFAULT '',
    total_xp INT NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_users_openid (openid)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS quiz_sessions (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    quiz_id VARCHAR(64) NOT NULL,
    user_id BIGINT UNSIGNED NULL,
    title VARCHAR(255) NOT NULL,
    summary TEXT NULL,
    user_input TEXT NULL,
    questions_json JSON NOT NULL,
    prompt_version VARCHAR(64) NULL,
    trace_id VARCHAR(64) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_quiz_sessions_quiz_id (quiz_id),
    KEY idx_quiz_sessions_user_id (user_id),
    KEY idx_quiz_sessions_created_at (created_at),
    CONSTRAINT fk_quiz_sessions_user_id
        FOREIGN KEY (user_id) REFERENCES users (id)
        ON DELETE SET NULL
        ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS answer_records (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    quiz_id VARCHAR(64) NOT NULL,
    user_id BIGINT UNSIGNED NULL,
    records_json JSON NOT NULL,
    total_questions INT NOT NULL,
    correct_count INT NOT NULL,
    accuracy DECIMAL(5, 2) NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_answer_records_quiz_id (quiz_id),
    KEY idx_answer_records_user_id (user_id),
    KEY idx_answer_records_created_at (created_at),
    CONSTRAINT fk_answer_records_user_id
        FOREIGN KEY (user_id) REFERENCES users (id)
        ON DELETE SET NULL
        ON UPDATE CASCADE,
    CONSTRAINT fk_answer_records_quiz_id
        FOREIGN KEY (quiz_id) REFERENCES quiz_sessions (quiz_id)
        ON DELETE CASCADE
        ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS reports (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    quiz_id VARCHAR(64) NOT NULL,
    user_id BIGINT UNSIGNED NULL,
    report_json JSON NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_reports_quiz_id (quiz_id),
    KEY idx_reports_user_id (user_id),
    KEY idx_reports_created_at (created_at),
    CONSTRAINT fk_reports_user_id
        FOREIGN KEY (user_id) REFERENCES users (id)
        ON DELETE SET NULL
        ON UPDATE CASCADE,
    CONSTRAINT fk_reports_quiz_id
        FOREIGN KEY (quiz_id) REFERENCES quiz_sessions (quiz_id)
        ON DELETE CASCADE
        ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 已有库的增量迁移（P0：prompt 版本与 trace 关联）。新库可忽略；重复执行会报列已存在，可安全忽略。
-- ALTER TABLE quiz_sessions ADD COLUMN prompt_version VARCHAR(64) NULL;
-- ALTER TABLE quiz_sessions ADD COLUMN trace_id VARCHAR(64) NULL;
