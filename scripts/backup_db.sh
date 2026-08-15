#!/bin/sh
# scripts/backup_db.sh
#
# Snapshot tinyagentos.db, encrypt it, upload to S3.
#
# Uses SQLite's own `.backup` command rather than `cp`/`cat` on the live
# file -- a raw file copy of a SQLite DB that's mid-write can produce a
# corrupt snapshot; `.backup` uses SQLite's own consistent-snapshot
# mechanism instead.
#
# Encryption is client-side (gpg, AES256) BEFORE upload, on top of
# whatever S3 server-side encryption the bucket has configured --
# defense in depth: even if bucket-level SSE is ever misconfigured or
# disabled, the object itself is still unreadable without
# BACKUP_GPG_PASSPHRASE.
#
# Required environment variables:
#   DATABASE_PATH          Path to the live SQLite file (e.g. /app/data/tinyagentos.db)
#   BACKUP_GPG_PASSPHRASE  Symmetric passphrase for encrypting the backup
#   S3_BACKUP_BUCKET       Target bucket, e.g. tinyagentos-backups
#   AWS credentials must be available via the environment/IAM role the
#   usual way (AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY, or an attached
#   IAM role if running in-cluster via IRSA/instance profile) -- not
#   handled by this script.

set -eu

: "${DATABASE_PATH:?DATABASE_PATH is required}"
: "${BACKUP_GPG_PASSPHRASE:?BACKUP_GPG_PASSPHRASE is required}"
: "${S3_BACKUP_BUCKET:?S3_BACKUP_BUCKET is required}"

TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
WORKDIR="$(mktemp -d)"
SNAPSHOT="${WORKDIR}/tinyagentos-${TIMESTAMP}.db"
ENCRYPTED="${SNAPSHOT}.gpg"

cleanup() {
  # Passphrase-derived key material and the plaintext snapshot both live
  # only in this tmpdir -- always remove it, success or failure.
  rm -rf "${WORKDIR}"
}
trap cleanup EXIT

echo "Backing up ${DATABASE_PATH} -> ${SNAPSHOT}"
sqlite3 "${DATABASE_PATH}" ".backup '${SNAPSHOT}'"

echo "Encrypting snapshot"
gpg --batch --yes --passphrase "${BACKUP_GPG_PASSPHRASE}" \
    --symmetric --cipher-algo AES256 \
    --output "${ENCRYPTED}" "${SNAPSHOT}"

DEST="s3://${S3_BACKUP_BUCKET}/tinyagentos/tinyagentos-${TIMESTAMP}.db.gpg"
echo "Uploading to ${DEST}"
# --sse aes256: bucket-level server-side encryption as well (defense in
# depth alongside the gpg encryption above). Use --sse aws:kms with
# --sse-kms-key-id instead if the bucket is set up for KMS-managed keys.
aws s3 cp "${ENCRYPTED}" "${DEST}" --sse aes256

echo "Backup complete: ${DEST}"
