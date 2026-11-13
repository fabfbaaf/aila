-- 在本机 PostgreSQL 里建库，用超级用户执行一次（会提示输入 postgres 用户的密码）：
--   "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U postgres -f scripts\create-db.sql
-- 这里的密码只用于本机开发；正式部署用 AILA_DB_PASSWORD 换成别的。
-- 表结构不用手动建，服务启动时 Flyway 会自动建。

create role aila login password 'aila';
create database aila owner aila encoding 'UTF8';
