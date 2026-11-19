# aila-server 的镜像：多阶段构建，运行时只有 JRE 和打好的 jar，用普通用户跑。
# 一般不单独用，配合 deploy/server/docker-compose.yml（带 PostgreSQL、可选 HTTPS），见 deploy/server/README.md。

# ---------- 构建 ----------
FROM eclipse-temurin:21-jdk AS build
WORKDIR /src
# 先只拷 pom 拉依赖：代码改了、依赖没改时这一层走缓存
COPY .mvn .mvn
COPY mvnw pom.xml ./
RUN chmod +x mvnw && ./mvnw -q -B dependency:go-offline
COPY src src
# 测试在仓库里跑（要内嵌的 PostgreSQL），镜像里只打包
RUN ./mvnw -q -B -DskipTests package \
 && find target -maxdepth 1 -name 'aila-server-*.jar' ! -name '*-plain.jar' -exec cp {} /app.jar \;

# ---------- 运行 ----------
FROM eclipse-temurin:21-jre
RUN groupadd --system aila && useradd --system --gid aila --uid 10001 --no-create-home aila
WORKDIR /app
COPY --from=build /app.jar app.jar
USER aila
EXPOSE 8790
# 配置都走环境变量：AILA_DB_URL / AILA_DB_USER / AILA_DB_PASSWORD / AILA_JWT_SECRET / AILA_FORWARD_HEADERS
ENTRYPOINT ["java", "-XX:MaxRAMPercentage=75", "-jar", "/app/app.jar"]
