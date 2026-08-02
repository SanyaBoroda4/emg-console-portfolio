FROM node:20-alpine

WORKDIR /app

# Install dependencies first so this layer is cached across code changes.
COPY package.json package-lock.json* ./
RUN npm install

COPY . .

EXPOSE 5173

CMD ["npm", "run", "dev"]
