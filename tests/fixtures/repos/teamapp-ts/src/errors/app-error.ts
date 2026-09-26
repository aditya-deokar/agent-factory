/** Base class for errors the API turns into HTTP responses. */
export class AppError extends Error {
  constructor(message: string, public readonly status: number = 500) {
    super(message);
  }
}

export class NotFoundError extends AppError {
  constructor(what: string) {
    super(`${what} not found`, 404);
  }
}

export class ValidationError extends AppError {
  constructor(message: string) {
    super(message, 400);
  }
}

export class ExpiredTokenError extends AppError {
  constructor() {
    super('token expired or already used', 410);
  }
}
