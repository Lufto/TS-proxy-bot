import winston from 'winston';
import DailyRotateFile from 'winston-daily-rotate-file';
import path from 'path';
import fs from 'fs';

const {
  combine, timestamp,
  label, printf,
} = winston.format;

const logFormat = printf(({
  level,
  message,
  label: logLabel,
  timestamp: logTimestamp,
  ...metadata
}) => {
  const metadataString = Object.keys(metadata).length > 0 ? `\n${JSON.stringify(metadata, null, 2)}` : '';
  return `${logTimestamp} [${logLabel}] ${level}: ${message}${metadataString}`;
});

// это нужно для того чтобы в edge кружении не было логера, потому что там нету node.js

const noopLogger: any = {
  info: () => {},
  error: () => {},
  warn: () => {},
  log: () => {},
};

const globalForLogger = globalThis as typeof globalThis & {
  loggerInstance?: winston.Logger;
};

const getLogger = () => {
  if (typeof process === 'undefined' || !process.versions?.node) return noopLogger;
  if (globalForLogger.loggerInstance) return globalForLogger.loggerInstance;

  const logDir = path.join(process.cwd(), 'logs');

  if (!fs.existsSync(logDir)) {
    fs.mkdirSync(logDir);
  }

  const dailyRotateTransport = new DailyRotateFile({
    filename: path.join(logDir, 'api-%DATE%.log'),
    datePattern: 'DD-MM-YYYY',
    zippedArchive: false,
    maxSize: process.env.API_LOG_MAX_SIZE || '20m',
    maxFiles: Number(process.env.API_LOG_MAX_FILES) || 50,
  });

  const loggerInstance = winston.createLogger({
    level: 'info',
    format: combine(
      label({ label: 'api-service' }),
      timestamp(),
      logFormat,
    ),
    transports: [
      dailyRotateTransport,
    ],
  });

  globalForLogger.loggerInstance = loggerInstance;
  return loggerInstance;
};

const logger: any = {
  info: (...args: any[]) => getLogger().info(...args),
  error: (...args: any[]) => getLogger().error(...args),
  warn: (...args: any[]) => getLogger().warn(...args),
  log: (...args: any[]) => getLogger().log(...args),
};

export default logger;
