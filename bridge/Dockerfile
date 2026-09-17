FROM node:22-slim
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev && mkdir /data && chown node /data
COPY *.mjs ./
USER node
CMD ["node", "index.mjs"]
