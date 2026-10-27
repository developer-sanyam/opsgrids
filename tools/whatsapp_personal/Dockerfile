FROM node:20-alpine

WORKDIR /app

COPY package*.json ./
RUN npm ci --omit=dev

COPY . .

ENV NODE_ENV=production
ENV WA_BRIDGE_PORT=3100
ENV WA_AUTH_DIR=/data/auth_info_baileys

VOLUME ["/data"]

EXPOSE 3100

CMD ["node", "server.js"]
