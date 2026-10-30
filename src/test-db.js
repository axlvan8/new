import { db } from './db.js';

try {
  const result = await db.query(
    'SELECT current_database() AS database, current_user AS username'
  );

  console.log('Database connection successful:', result.rows[0]);
} catch (error) {
  console.error('Database connection failed:', error.message);
  process.exitCode = 1;
} finally {
  await db.end();
}
