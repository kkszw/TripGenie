FROM docker.m.daocloud.io/library/node:22-alpine AS build
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --registry=https://registry.npmmirror.com

COPY . .

# Vite 是构建期内联，这两个值必须在 build 时给，运行时再设环境变量无效
ARG VITE_AMAP_JS_KEY
ARG VITE_AMAP_SECURITY_CODE
ENV VITE_AMAP_JS_KEY=$VITE_AMAP_JS_KEY \
    VITE_AMAP_SECURITY_CODE=$VITE_AMAP_SECURITY_CODE

RUN npm run build

FROM docker.m.daocloud.io/library/nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
