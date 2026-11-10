-- Demo data of C09 (9xx - test data only): passwords of the demo users of 900 for Web lab 3.
--   user1 / user1-pass, user2 / user2-pass (role user), staff1 / staff1-pass (role staff).
-- argon2id hashes made by `uv run scripts/hash_password.py user1-pass user2-pass staff1-pass`.

UPDATE app_user SET password_hash = '$argon2id$v=19$m=65536,t=3,p=4$Umpk8CL6FBjGteu/5z0XCQ$QOiO8qRneML0ElPy0BqrI1OSxg4mQCjTrpAjgRbterw'
WHERE login = 'user1';
UPDATE app_user SET password_hash = '$argon2id$v=19$m=65536,t=3,p=4$6H+5PswN9b3Ypy+8bdofMA$EBUkqe+M45nNrVyNc8SkFEi9Ydc6FVSqBk2t6Uc5ke0'
WHERE login = 'user2';
UPDATE app_user SET password_hash = '$argon2id$v=19$m=65536,t=3,p=4$rw73Xn0SFazlemgD9kHJlQ$U/okzTqFTam3RjubZhj9spjf1kOffYYQwiI/nPQKkWE'
WHERE login = 'staff1';
