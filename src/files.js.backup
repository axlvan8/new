import { Hono } from 'hono';
import { db } from './db.js';
import { getSessionUser } from './auth.js';
import Busboy from 'busboy';
import {
  createWriteStream,
  createReadStream
} from 'node:fs';
import {
  mkdir,
  unlink
} from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import path from 'node:path';
import { pipeline } from 'node:stream/promises';

export const files = new Hono();

const STORAGE_DIR = '/mnt/nextcloud-data/myfirebase/files';

const MAX_UPLOAD = 100 * 1024 * 1024;
const PRIVATE_QUOTA = 5 * 1024 * 1024 * 1024;
const PUBLIC_QUOTA = 3 * 1024 * 1024 * 1024;

await mkdir(STORAGE_DIR, {
  recursive: true,
  mode: 0o700
});

function validId(id) {
  return /^[1-9]\d*$/.test(id);
}

async function requireUser(c) {
  const user = await getSessionUser(c);

  if (!user) {
    return null;
  }

  return user;
}

// List files accessible to the signed-in user
files.get('/', async (c) => {
  try {
    const user = await requireUser(c);

    if (!user) {
      return c.json({ error: 'Not authenticated' }, 401);
    }

    const result = await db.query(
      `SELECT DISTINCT f.id, f.owner_id, f.name,
              f.visibility, f.size_bytes, f.created_at
       FROM files f
       LEFT JOIN file_permissions p
         ON p.file_id = f.id
       WHERE f.owner_id = $1
          OR (p.user_id = $1 AND p.can_view = TRUE)
       ORDER BY f.created_at DESC`,
      [user.id]
    );

    return c.json({ data: result.rows });
  } catch (error) {
    console.error('List files failed:', error.message);
    return c.json({ error: 'Could not list files' }, 500);
  }
});

// Storage usage
files.get('/usage', async (c) => {
  try {
    const user = await requireUser(c);

    if (!user) {
      return c.json({ error: 'Not authenticated' }, 401);
    }

    const result = await db.query(
      `SELECT
         COALESCE(SUM(size_bytes) FILTER
           (WHERE visibility = 'private'), 0) AS private_used,
         COALESCE(SUM(size_bytes) FILTER
           (WHERE visibility = 'public'), 0) AS public_used
       FROM files
       WHERE owner_id = $1`,
      [user.id]
    );

    return c.json({
      private: {
        used: Number(result.rows[0].private_used),
        quota: PRIVATE_QUOTA
      },
      public: {
        used: Number(result.rows[0].public_used),
        quota: PUBLIC_QUOTA
      }
    });
  } catch (error) {
    console.error('Usage lookup failed:', error.message);
    return c.json({ error: 'Could not get storage usage' }, 500);
  }
});

// Upload a file
files.post('/upload', async (c) => {
  const user = await requireUser(c);

  if (!user) {
    return c.json({ error: 'Not authenticated' }, 401);
  }

  let storageKey = null;
  let fileName = null;
  let fileSize = 0;
  let visibility = 'private';
  let fileStream = null;
  let writePromise = null;
  let uploadError = null;

  try {
    const contentLength = Number(c.req.header('content-length'));

    if (contentLength > MAX_UPLOAD + 1024 * 1024) {
      return c.json({ error: 'Upload too large' }, 413);
    }

    const bb = Busboy({
      headers: Object.fromEntries(c.req.raw.headers),
      limits: {
        fileSize: MAX_UPLOAD,
        files: 1,
        fields: 2
      }
    });

    const bodyPromise = new Promise((resolve, reject) => {
      bb.on('file', (field, stream, info) => {
        if (field !== 'file' || fileStream) {
          stream.resume();
          uploadError = new Error('Invalid file field');
          return;
        }

        fileName = path.basename(info.filename || 'upload');

        if (!fileName || fileName.length > 255) {
          uploadError = new Error('Invalid filename');
          stream.resume();
          return;
        }

        storageKey = randomUUID();

        fileStream = stream;

        stream.on('data', (chunk) => {
          fileSize += chunk.length;
        });

        stream.on('limit', () => {
          uploadError = new Error('Upload exceeds 100 MB');
        });

        writePromise = pipeline(
          stream,
          createWriteStream(
            path.join(STORAGE_DIR, storageKey),
            { flags: 'wx', mode: 0o600 }
          )
        );
      });

      bb.on('field', (name, value) => {
        if (name === 'visibility') {
          visibility = value;
        }
      });

      bb.on('filesLimit', () => {
        uploadError = new Error('Only one file is allowed');
      });

      bb.on('error', reject);
      bb.on('close', resolve);
    });

    const requestBuffer = await c.req.arrayBuffer();

    if (requestBuffer.byteLength > MAX_UPLOAD + 1024 * 1024) {
      return c.json({ error: 'Upload too large' }, 413);
    }

    bb.end(Buffer.from(requestBuffer));

    await bodyPromise;

    if (writePromise) {
      await writePromise;
    }

    if (uploadError) {
      throw uploadError;
    }

    if (!storageKey || !fileName || fileSize === 0) {
      throw new Error('No valid file was uploaded');
    }

    if (!['private', 'public'].includes(visibility)) {
      throw new Error('Invalid visibility');
    }

    const quota = visibility === 'private'
      ? PRIVATE_QUOTA
      : PUBLIC_QUOTA;

    const client = await db.connect();

    try {
      await client.query('BEGIN');

      const usage = await client.query(
        `SELECT COALESCE(SUM(size_bytes), 0) AS used
         FROM files
         WHERE owner_id = $1
           AND visibility = $2
         FOR UPDATE`,
        [user.id, visibility]
      );

      if (Number(usage.rows[0].used) + fileSize > quota) {
        throw new Error('Storage quota exceeded');
      }

      const result = await client.query(
        `INSERT INTO files
         (owner_id, name, storage_key, visibility, size_bytes)
         VALUES ($1, $2, $3, $4, $5)
         RETURNING id, name, visibility, size_bytes, created_at`,
        [user.id, fileName, storageKey, visibility, fileSize]
      );

      await client.query('COMMIT');

      return c.json({ data: result.rows[0] }, 201);
    } catch (error) {
      await client.query('ROLLBACK');
      throw error;
    } finally {
      client.release();
    }
  } catch (error) {
    if (storageKey) {
      await unlink(path.join(STORAGE_DIR, storageKey))
        .catch(() => {});
    }

    console.error('Upload failed:', error.message);

    if (error.message.includes('quota')) {
      return c.json({ error: 'Storage quota exceeded' }, 413);
    }

    if (error.message.includes('100 MB') ||
        error.message.includes('too large')) {
      return c.json({ error: 'Upload exceeds 100 MB' }, 413);
    }

    return c.json({ error: 'Upload failed' }, 400);
  }
});

// Download a file
files.get('/:id/download', async (c) => {
  const user = await requireUser(c);

  const id = c.req.param('id');

  if (!validId(id)) {
    return c.json({ error: 'Invalid file ID' }, 400);
  }

  try {
    const result = await db.query(
      `SELECT f.*
       FROM files f
       LEFT JOIN file_permissions p
         ON p.file_id = f.id AND p.user_id = $2
       WHERE f.id = $1
         AND (
           f.owner_id = $2
           OR f.visibility = 'public'
           OR (p.can_view = TRUE)
         )`,
      [id, user?.id ?? null]
    );

    if (!result.rowCount) {
      return c.json({ error: 'File not found or access denied' }, 404);
    }

    const file = result.rows[0];

    const filePath = path.join(STORAGE_DIR, file.storage_key);

    return new Response(
      createReadStream(filePath),
      {
        headers: {
          'Content-Type': 'application/octet-stream',
          'Content-Disposition':
            `attachment; filename*=UTF-8''${encodeURIComponent(file.name)}`,
          'Content-Length': String(file.size_bytes),
          'X-Content-Type-Options': 'nosniff'
        }
      }
    );
  } catch (error) {
    console.error('Download failed:', error.message);
    return c.json({ error: 'Download failed' }, 500);
  }
});

// Delete a file
files.delete('/:id', async (c) => {
  const user = await requireUser(c);

  if (!user) {
    return c.json({ error: 'Not authenticated' }, 401);
  }

  const id = c.req.param('id');

  if (!validId(id)) {
    return c.json({ error: 'Invalid file ID' }, 400);
  }

  try {
    const client = await db.connect();

    try {
      await client.query('BEGIN');

      const result = await client.query(
        `SELECT f.*
         FROM files f
         LEFT JOIN file_permissions p
           ON p.file_id = f.id AND p.user_id = $2
         WHERE f.id = $1
           AND (f.owner_id = $2 OR p.can_delete = TRUE)
         FOR UPDATE OF f`,
        [id, user.id]
      );

      if (!result.rowCount) {
        await client.query('ROLLBACK');
        return c.json({ error: 'File not found or access denied' }, 404);
      }

      const file = result.rows[0];

      await client.query(
        'DELETE FROM files WHERE id = $1',
        [id]
      );

      await client.query('COMMIT');

      await unlink(path.join(STORAGE_DIR, file.storage_key))
        .catch((error) => {
          console.error('Stored file cleanup failed:', error.message);
        });

      return c.json({ deleted: true, id: file.id });
    } catch (error) {
      await client.query('ROLLBACK');
      throw error;
    } finally {
      client.release();
    }
  } catch (error) {
    console.error('Delete failed:', error.message);
    return c.json({ error: 'Could not delete file' }, 500);
  }
});
