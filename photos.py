"""Recipe photos: accept whatever came off the phone, store something small.

Nothing the visitor sends is ever written to disk as-is. Every upload is
decoded, rotated upright, scaled down, and re-encoded as a fresh JPEG. That
one rule handles most of what makes uploads risky -- a file pretending to be
an image does not survive being decoded and re-encoded, and re-encoding drops
any metadata that came with it.

Dropping the metadata matters for a family site specifically: phone photos
carry the GPS coordinates of the kitchen they were taken in, and those must
not end up on a public website.
"""

import io
import os
import re
import secrets

from PIL import Image, ImageOps

import db

# iPhones hand out HEIC. Without this, half the family's photos would bounce.
try:
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIC_SUPPORTED = True
except Exception:                               # noqa: BLE001 - optional extra
    HEIC_SUPPORTED = False

# A "decompression bomb" is a small file that expands into an enormous bitmap
# and exhausts the server's memory. 50 megapixels is far past any real photo.
Image.MAX_IMAGE_PIXELS = 50_000_000

MAX_PER_RECIPE = 5
# 720p class. Sized so a single photo filling the banner across a wide screen
# is never upscaled -- the banner tops out around 1140px.
MAX_EDGE = 1280
JPEG_QUALITY = 82
MAX_UPLOAD_BYTES = 12 * 1024 * 1024

FILENAME_RE = re.compile(r"^[a-f0-9]{32}\.jpg$")


class PhotoError(Exception):
    """Raised with a message that is fine to show the person uploading."""


def photo_dir():
    path = os.path.join(db.data_dir(), "photos")
    os.makedirs(path, exist_ok=True)
    return path


def _flatten(image):
    """Put transparent images on white instead of black."""
    if image.mode in ("RGBA", "LA") or (
        image.mode == "P" and "transparency" in image.info
    ):
        image = image.convert("RGBA")
        canvas = Image.new("RGB", image.size, (255, 255, 255))
        canvas.paste(image, mask=image.split()[-1])
        return canvas
    if image.mode != "RGB":
        return image.convert("RGB")
    return image


def save(file_storage):
    """Process one upload and write it out. Returns (filename, width, height)."""
    data = file_storage.read(MAX_UPLOAD_BYTES + 1)
    if not data:
        raise PhotoError("that file was empty")
    if len(data) > MAX_UPLOAD_BYTES:
        raise PhotoError(
            "that photo is larger than %d MB" % (MAX_UPLOAD_BYTES // (1024 * 1024))
        )

    try:
        # verify() is a cheap structural check, but it leaves the image
        # unusable, so the real work happens on a second open.
        Image.open(io.BytesIO(data)).verify()
        image = Image.open(io.BytesIO(data))
        image = ImageOps.exif_transpose(image)   # honour "rotate 90" from phones
        image = _flatten(image)
        image.thumbnail((MAX_EDGE, MAX_EDGE), Image.LANCZOS)
    except PhotoError:
        raise
    except Exception:                           # noqa: BLE001 - any decode failure
        raise PhotoError(
            "that file could not be read as a photo"
            + ("" if HEIC_SUPPORTED else " (HEIC photos are not supported here)")
        )

    buffer = io.BytesIO()
    # No exif= argument, so the location and camera data are simply not written.
    image.save(buffer, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)

    filename = secrets.token_hex(16) + ".jpg"
    with open(os.path.join(photo_dir(), filename), "wb") as handle:
        handle.write(buffer.getvalue())
    return filename, image.width, image.height


def remove(filename):
    """Delete a stored photo. Never raises: a missing file is already the goal."""
    if not filename or not FILENAME_RE.match(filename):
        return
    try:
        os.remove(os.path.join(photo_dir(), filename))
    except OSError:
        pass


def for_recipe(conn, recipe_id):
    return conn.execute(
        "SELECT * FROM photos WHERE recipe_id = ? ORDER BY position, id",
        (recipe_id,),
    ).fetchall()


def count_for(conn, recipe_id):
    return conn.execute(
        "SELECT COUNT(*) AS n FROM photos WHERE recipe_id = ?", (recipe_id,)
    ).fetchone()["n"]


def attach(conn, recipe_id, uploads):
    """Store new uploads against a recipe.

    Returns a list of complaints to show the user. Photo trouble is never fatal
    -- losing a typed-out recipe because one picture was odd would be a far
    worse outcome than a recipe with a missing photo.
    """
    problems = []
    uploads = [f for f in uploads if f and f.filename]
    if not uploads:
        return problems

    room = MAX_PER_RECIPE - count_for(conn, recipe_id)
    if room <= 0:
        return ["This recipe already has %d photos, which is the limit."
                % MAX_PER_RECIPE]
    if len(uploads) > room:
        problems.append(
            "Only the first %d photo%s were added, because %d is the limit per "
            "recipe." % (room, "" if room == 1 else "s", MAX_PER_RECIPE)
        )
        uploads = uploads[:room]

    position = conn.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 AS n FROM photos WHERE recipe_id = ?",
        (recipe_id,),
    ).fetchone()["n"]

    for upload in uploads:
        try:
            filename, width, height = save(upload)
        except PhotoError as exc:
            problems.append("One photo was skipped: %s." % exc)
            continue
        conn.execute(
            "INSERT INTO photos(recipe_id, filename, position, width, height, "
            "created_at) VALUES(?,?,?,?,?,?)",
            (recipe_id, filename, position, width, height, db.now()),
        )
        position += 1
    return problems


def detach(conn, recipe_id, photo_ids):
    """Remove selected photos from a recipe, files included."""
    for raw in photo_ids:
        try:
            photo_id = int(raw)
        except (TypeError, ValueError):
            continue
        row = conn.execute(
            "SELECT * FROM photos WHERE id = ? AND recipe_id = ?",
            (photo_id, recipe_id),
        ).fetchone()
        if row is None:
            continue
        conn.execute("DELETE FROM photos WHERE id = ?", (photo_id,))
        remove(row["filename"])


def detach_all(conn, recipe_id):
    """Used when a whole recipe goes: the rows cascade, the files do not."""
    for row in for_recipe(conn, recipe_id):
        remove(row["filename"])
