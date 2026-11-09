#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Zotero Local Library Lookup & Automatic Attachment Mounter for academic-paper-translator.
"""
import argparse
from datetime import datetime
import json
import os
import random
import shutil
import sqlite3
import time

CHARS = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
DEFAULT_DB = r"E:\Document\Zotero\zotero.sqlite"


def gen_key(existing_keys: set[str]) -> str:
    while True:
        k = "".join(random.choice(CHARS) for _ in range(8))
        if k not in existing_keys:
            return k


def find_paper(db_path: str, query: str) -> list[dict]:
    storage_root = os.path.join(os.path.dirname(db_path), "storage")
    conn = sqlite3.connect(f"file:{db_path}?immutable=1", uri=True)
    cur = conn.cursor()
    q = f"%{query}%"
    rows = cur.execute(
        """
        SELECT DISTINCT items.itemID, items.key, itemDataValues.value
        FROM items
        JOIN itemData ON items.itemID = itemData.itemID
        JOIN fields ON itemData.fieldID = fields.fieldID
        JOIN itemDataValues ON itemData.valueID = itemDataValues.valueID
        WHERE fields.fieldName IN ('title', 'DOI')
          AND itemDataValues.value LIKE ?
          AND items.itemID NOT IN (SELECT itemID FROM deletedItems)
          AND items.itemTypeID != 3
        """,
        (q,),
    ).fetchall()

    results = []
    for item_id, key, val in rows:
        meta = {}
        for fn, fv in cur.execute(
            """
            SELECT fields.fieldName, itemDataValues.value
            FROM itemData
            JOIN fields ON itemData.fieldID = fields.fieldID
            JOIN itemDataValues ON itemData.valueID = itemDataValues.valueID
            WHERE itemData.itemID = ?
            """,
            (item_id,),
        ):
            meta[fn] = fv
        atts = []
        for att_id, att_key, ctype, path in cur.execute(
            """
            SELECT itemAttachments.itemID, items.key, itemAttachments.contentType, itemAttachments.path
            FROM itemAttachments
            JOIN items ON itemAttachments.itemID = items.itemID
            WHERE itemAttachments.parentItemID = ?
              AND items.itemID NOT IN (SELECT itemID FROM deletedItems)
            """,
            (item_id,),
        ):
            full_path = None
            if path and path.startswith("storage:"):
                rel = path[len("storage:") :]
                full_path = os.path.join(storage_root, att_key, rel)
            atts.append(
                {
                    "itemID": att_id,
                    "key": att_key,
                    "contentType": ctype,
                    "path": path,
                    "resolvedPath": full_path,
                }
            )
        results.append(
            {
                "itemID": item_id,
                "key": key,
                "metadata": meta,
                "attachments": atts,
            }
        )
    conn.close()
    return results


def mount_attachments(
    db_path: str,
    parent_id: int,
    bilingual_pdf: str | None = None,
    zh_pdf: str | None = None,
    title_bi: str | None = None,
    title_zh: str | None = None,
) -> dict:
    storage_root = os.path.join(os.path.dirname(db_path), "storage")

    # First check via immutable read-only connection if attachments already exist
    ro_conn = sqlite3.connect(f"file:{db_path}?immutable=1", uri=True)
    ro_cur = ro_conn.cursor()
    existing_map = {}
    for att_id, key, path in ro_cur.execute(
        """
        SELECT itemAttachments.itemID, items.key, itemAttachments.path
        FROM itemAttachments
        JOIN items ON itemAttachments.itemID = items.itemID
        WHERE itemAttachments.parentItemID = ?
          AND items.itemID NOT IN (SELECT itemID FROM deletedItems)
        """,
        (parent_id,),
    ).fetchall():
        existing_map[path] = (att_id, key)
    ro_conn.close()

    to_insert = []
    mounted = []

    for pdf_path, custom_title, suffix_label in [
        (bilingual_pdf, title_bi, "Bilingual / 双语对照版"),
        (zh_pdf, title_zh, "Chinese Monograph / 中文专著版"),
    ]:
        if not pdf_path or not os.path.isfile(pdf_path):
            continue
        pdf_name = os.path.basename(pdf_path)
        sp = f"storage:{pdf_name}"
        if sp in existing_map:
            att_id, key = existing_map[sp]
            dst_dir = os.path.join(storage_root, key)
            os.makedirs(dst_dir, exist_ok=True)
            dst_file = os.path.join(dst_dir, pdf_name)
            shutil.copy2(pdf_path, dst_file)
            mounted.append({"itemID": att_id, "key": key, "path": dst_file, "updated": True})
        else:
            base = os.path.splitext(pdf_name)[0]
            to_insert.append((pdf_path, custom_title or f"{base} ({suffix_label})"))

    if not to_insert:
        return {"backup": None, "parentItemID": parent_id, "mounted": mounted}

    bak_path = f"{db_path}.bak_{int(time.time())}"
    shutil.copy2(db_path, bak_path)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    existing_keys = {r[0] for r in cur.execute("SELECT key FROM items").fetchall()}
    max_item_id = cur.execute("SELECT MAX(itemID) FROM items").fetchone()[0] or 0
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for pdf_path, att_title in to_insert:
        pdf_name = os.path.basename(pdf_path)
        max_item_id += 1
        item_id = max_item_id
        key = gen_key(existing_keys)
        existing_keys.add(key)
        dst_dir = os.path.join(storage_root, key)
        os.makedirs(dst_dir, exist_ok=True)
        dst_file = os.path.join(dst_dir, pdf_name)
        shutil.copy2(pdf_path, dst_file)
        mtime = int(os.path.getmtime(dst_file))

        cur.execute(
            """
            INSERT INTO items (itemID, itemTypeID, dateAdded, dateModified, clientDateModified, libraryID, key, version, synced, clientVersion)
            VALUES (?, 3, ?, ?, ?, 1, ?, 0, 0, 0)
            """,
            (item_id, now_str, now_str, now_str, key),
        )
        cur.execute("INSERT INTO itemDataValues (value) VALUES (?)", (att_title,))
        val_id = cur.lastrowid
        cur.execute(
            "INSERT INTO itemData (itemID, fieldID, valueID) VALUES (?, 1, ?)",
            (item_id, val_id),
        )
        cur.execute(
            """
            INSERT INTO itemAttachments (itemID, parentItemID, linkMode, contentType, charsetID, path, syncState, storageModTime, storageHash, lastProcessedModificationTime, lastRead)
            VALUES (?, ?, 0, 'application/pdf', NULL, ?, 0, ?, NULL, ?, 0)
            """,
            (item_id, parent_id, f"storage:{pdf_name}", mtime, mtime),
        )
        mounted.append({"itemID": item_id, "key": key, "path": dst_file, "updated": False})

    conn.commit()
    conn.close()
    return {"backup": bak_path, "parentItemID": parent_id, "mounted": mounted}


def main():
    parser = argparse.ArgumentParser(description="Zotero lookup and attachment mounter")
    parser.add_argument("--db", default=DEFAULT_DB, help="Path to zotero.sqlite")
    parser.add_argument("--find", help="Search paper by title or DOI substring")
    parser.add_argument("--parent-id", type=int, help="Parent itemID in Zotero to mount under")
    parser.add_argument("--bilingual", help="Path to compiled bilingual PDF")
    parser.add_argument("--zh", help="Path to compiled Chinese monograph PDF")
    parser.add_argument("--title-bi", help="Custom attachment title for bilingual PDF")
    parser.add_argument("--title-zh", help="Custom attachment title for Chinese PDF")
    args = parser.parse_args()

    if args.find:
        res = find_paper(args.db, args.find)
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif args.parent_id:
        res = mount_attachments(
            args.db,
            args.parent_id,
            bilingual_pdf=args.bilingual,
            zh_pdf=args.zh,
            title_bi=args.title_bi,
            title_zh=args.title_zh,
        )
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
