FROM node:20-alpine

WORKDIR /app

COPY package*.json ./

ARG NPM_REGISTRY=https://registry.npmjs.org

RUN npm config set registry ${NPM_REGISTRY} \
    && npm config set fetch-retries 10 \
    && npm config set fetch-retry-factor 2 \
    && npm config set fetch-retry-mintimeout 30000 \
    && npm config set fetch-retry-maxtimeout 300000 \
    && npm config set fetch-timeout 300000 \
    && npm config set prefer-offline false \
    && if [ -f package-lock.json ]; then npm ci --no-audit --no-fund; else npm install --no-audit --no-fund; fi

COPY . .

EXPOSE 5174

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]