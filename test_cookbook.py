"""Tests for the parts that would quietly ruin the site if they broke:
ingredient parsing (a wrong amount is worse than no website), moderation (both
letting bad things through and blocking innocent recipes), and photo handling
(where "it is an image" is an assumption, not a fact).

Run with:  python3 -m unittest test_cookbook -v
"""

import io
import os
import shutil
import tempfile
import unittest
import urllib.error
import urllib.request

import db
import mailer
import moderation
import units


class ParsingTests(unittest.TestCase):

    def parse(self, line):
        return units.parse_ingredient(line)

    def test_whole_number_with_unit(self):
        got = self.parse("2 cups flour")
        self.assertEqual(got["qty"], 2)
        self.assertEqual(got["unit"], "cup")
        self.assertEqual(got["item"], "flour")

    def test_mixed_fraction(self):
        got = self.parse("1 1/2 teaspoons cinnamon")
        self.assertAlmostEqual(got["qty"], 1.5)
        self.assertEqual(got["unit"], "tsp")

    def test_bare_fraction(self):
        got = self.parse("3/4 cup sugar")
        self.assertAlmostEqual(got["qty"], 0.75)
        self.assertEqual(got["unit"], "cup")

    def test_unicode_fraction(self):
        got = self.parse("½ cup butter")
        self.assertAlmostEqual(got["qty"], 0.5)
        self.assertEqual(got["unit"], "cup")

    def test_unicode_fraction_glued_to_whole(self):
        got = self.parse("1½ cups milk")
        self.assertAlmostEqual(got["qty"], 1.5)
        self.assertEqual(got["item"], "milk")

    def test_range(self):
        got = self.parse("2-3 cloves garlic")
        self.assertEqual(got["qty"], 2)
        self.assertEqual(got["qty_max"], 3)
        self.assertEqual(got["unit"], "clove")

    def test_no_unit(self):
        got = self.parse("6 apples, peeled and sliced")
        self.assertEqual(got["qty"], 6)
        self.assertEqual(got["unit"], "")
        self.assertEqual(got["item"], "apples, peeled and sliced")

    def test_capital_T_is_tablespoon_and_lowercase_t_is_teaspoon(self):
        self.assertEqual(self.parse("2 T butter")["unit"], "tbsp")
        self.assertEqual(self.parse("2 t vanilla")["unit"], "tsp")

    def test_abbreviations_with_periods(self):
        self.assertEqual(self.parse("1 tbsp. olive oil")["unit"], "tbsp")
        self.assertEqual(self.parse("3 oz. cream cheese")["unit"], "oz")

    def test_placeholder_ingredients_scale_correctly(self):
        # These two lines are the example shown in the add-recipe form, so they
        # are the ones people are most likely to copy.
        bread = self.parse("1 loaf of french bread")
        self.assertEqual(bread["qty"], 1)
        self.assertEqual(bread["unit"], "loaf")
        self.assertEqual(units.describe(bread, 1), "1 loaf of french bread")
        self.assertEqual(units.describe(bread, 2), "2 loaves of french bread")

        cheese = self.parse("8 oz of cream cheese")
        self.assertEqual(cheese["qty"], 8)
        self.assertEqual(cheese["unit"], "oz")
        self.assertEqual(units.describe(cheese, 1), "8 ounces of cream cheese")

    def test_no_amount_stays_literal(self):
        got = self.parse("salt to taste")
        self.assertIsNone(got["qty"])
        self.assertEqual(got["item"], "salt to taste")

    def test_heading(self):
        got = self.parse("For the topping:")
        self.assertTrue(got["is_heading"])
        self.assertEqual(got["item"], "For the topping")

    def test_parenthetical_size_is_not_treated_as_the_amount(self):
        got = self.parse("2 cans (28 oz) crushed tomatoes")
        self.assertEqual(got["qty"], 2)
        self.assertEqual(got["unit"], "can")
        self.assertIn("28 oz", got["item"])

    def test_unit_with_no_food_named_still_scales(self):
        # "3 cups" on its own is an incomplete line, but the number is real, so
        # scaling it is still right. The word "cups" just rides along as text.
        got = self.parse("3 cups")
        self.assertEqual(got["qty"], 3)
        self.assertEqual(units.describe(got, 2), "6 cups")

    def test_a_number_in_a_name_does_not_become_an_amount(self):
        got = self.parse("7 Up cake mix")
        self.assertEqual(got["qty"], 7)
        self.assertEqual(got["unit"], "")


class FormattingTests(unittest.TestCase):

    def test_whole_numbers(self):
        self.assertEqual(units.format_amount(2), "2")
        self.assertEqual(units.format_amount(2.0), "2")

    def test_common_fractions(self):
        self.assertEqual(units.format_amount(0.5), "1/2")
        self.assertEqual(units.format_amount(0.75), "3/4")
        self.assertEqual(units.format_amount(1.5), "1 1/2")
        self.assertEqual(units.format_amount(1 / 3), "1/3")
        self.assertEqual(units.format_amount(2 / 3), "2/3")
        self.assertEqual(units.format_amount(0.125), "1/8")

    def test_halving_three_quarters_gives_three_eighths(self):
        self.assertEqual(units.format_amount(0.75 / 2), "3/8")

    def test_thirds_of_awkward_amounts_stay_readable(self):
        self.assertEqual(units.format_amount(1.5 / 3), "1/2")

    def test_plural_labels(self):
        self.assertEqual(units.unit_label("cup", 1), "cup")
        self.assertEqual(units.unit_label("cup", 2), "cups")
        self.assertEqual(units.unit_label("", 2), "")


class ModerationTests(unittest.TestCase):

    def assertAllowed(self, text):
        try:
            moderation.check(text, "comment")
        except moderation.Rejected as exc:
            self.fail("should have been allowed: %r (%s)" % (text, exc))

    def assertBlocked(self, text):
        with self.assertRaises(moderation.Rejected):
            moderation.check(text, "comment")

    def test_ordinary_cooking_notes_pass(self):
        self.assertAllowed("I bake it 5 minutes longer and use half the sugar.")
        self.assertAllowed("Didn't work for me -- the dough was way too wet.")
        self.assertAllowed("My kids hated this one, but I liked it!")

    def test_innocent_words_are_not_caught_by_substrings(self):
        # The classic filter failures. All of these must pass.
        self.assertAllowed("1 cup molasses")
        self.assertAllowed("Serve with a cocktail and shiitake mushrooms.")
        self.assertAllowed("Grandma grew up in Scunthorpe.")
        self.assertAllowed("Assemble the casserole the night before.")
        self.assertAllowed("Use Assam tea for the glaze.")
        self.assertAllowed("Add the bass fillets and the class of 1962 loved it.")

    def test_profanity_is_blocked(self):
        self.assertBlocked("this recipe is fucking terrible")

    def test_obfuscated_profanity_is_blocked(self):
        self.assertBlocked("this is sh1t")
        self.assertBlocked("f u c k this recipe")

    def test_personal_insults_are_blocked_but_recipe_criticism_is_not(self):
        self.assertBlocked("you are an idiot for posting this")
        self.assertAllowed("This recipe is a disaster and I would not make it again.")

    def test_threats_are_blocked(self):
        self.assertBlocked("i will hurt you")

    def test_spam_is_blocked(self):
        self.assertBlocked("Buy now at my store for cheap meds")
        self.assertBlocked("Check out https://example.com/deals")
        self.assertBlocked("email me at someone@example.com")

    def test_shouting_is_blocked(self):
        self.assertBlocked("THIS IS THE BEST RECIPE I HAVE EVER MADE IN MY LIFE")

    def test_short_capitalized_words_are_fine(self):
        self.assertAllowed("Bake at 350 F. IMPORTANT: do not open the oven.")

    def test_links_allowed_when_the_field_permits_them(self):
        moderation.check("From https://example.com", "source", allow_links=True)

    def test_names(self):
        self.assertEqual(moderation.check_name("  Mary Valentine "), "Mary Valentine")
        with self.assertRaises(moderation.Rejected):
            moderation.check_name("Mary")
        with self.assertRaises(moderation.Rejected):
            moderation.check_name("")
        self.assertEqual(moderation.check_name("Jean-Luc O'Brien"), "Jean-Luc O'Brien")


class PhotoTests(unittest.TestCase):
    """Photo content is not moderated by choice, but the file handling still
    has to hold up: uploads are the classic way a site gets taken over."""

    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.mkdtemp()
        os.environ["DATA_DIR"] = cls.tempdir
        global photos, Image
        import photos                            # noqa: PLW0603 - after DATA_DIR
        from PIL import Image                    # noqa: PLW0603

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tempdir, ignore_errors=True)
        os.environ.pop("DATA_DIR", None)

    def upload(self, data, name="photo.jpg"):
        """Stand in for a Werkzeug FileStorage, which is just .read()/.filename."""
        class Upload(object):
            filename = name

            def __init__(self, blob):
                self._stream = io.BytesIO(blob)

            def read(self, size=-1):
                return self._stream.read(size)

        return Upload(data)

    def make_image(self, width, height, fmt="JPEG", color=(200, 80, 80)):
        buffer = io.BytesIO()
        Image.new("RGB", (width, height), color).save(buffer, fmt)
        return buffer.getvalue()

    def test_large_photo_is_shrunk_to_480p_class(self):
        name, width, height = photos.save(self.upload(self.make_image(4032, 3024)))
        self.assertEqual(max(width, height), photos.MAX_EDGE)
        self.assertTrue(name.endswith(".jpg"))
        self.assertTrue(photos.FILENAME_RE.match(name))

    def test_portrait_photo_keeps_its_shape(self):
        _, width, height = photos.save(self.upload(self.make_image(3024, 4032)))
        self.assertEqual(height, photos.MAX_EDGE)
        self.assertLess(width, height)

    def test_small_photo_is_not_enlarged(self):
        _, width, height = photos.save(self.upload(self.make_image(300, 200)))
        self.assertEqual((width, height), (300, 200))

    def test_camera_and_location_metadata_is_stripped(self):
        # A photo off a phone carries the camera it came from and, worse, the
        # GPS coordinates it was taken at -- the address of somebody's kitchen,
        # heading for a public website. None of it may survive.
        original = Image.new("RGB", (600, 400), (10, 20, 30))
        exif = original.getexif()
        exif[0x0110] = "iPhone 15"                          # Model
        exif[0x0132] = "2026:07:27 10:00:00"                # DateTime
        exif[0x010E] = "Taken at 40.7128N 74.0060W"         # ImageDescription
        buffer = io.BytesIO()
        original.save(buffer, "JPEG", exif=exif)
        self.assertIn(b"iPhone 15", buffer.getvalue())      # present going in

        name, _, _ = photos.save(self.upload(buffer.getvalue()))
        with open(os.path.join(photos.photo_dir(), name), "rb") as handle:
            stored = handle.read()
        self.assertNotIn(b"iPhone 15", stored)
        self.assertNotIn(b"40.7128N", stored)
        with Image.open(io.BytesIO(stored)) as reopened:
            self.assertEqual(len(reopened.getexif()), 0)

    def test_transparent_png_lands_on_white_not_black(self):
        buffer = io.BytesIO()
        Image.new("RGBA", (100, 100), (255, 255, 255, 0)).save(buffer, "PNG")
        name, _, _ = photos.save(self.upload(buffer.getvalue(), "clear.png"))
        with Image.open(os.path.join(photos.photo_dir(), name)) as stored:
            self.assertEqual(stored.mode, "RGB")
            self.assertEqual(stored.getpixel((50, 50)), (255, 255, 255))

    def test_everything_is_re_encoded_as_jpeg(self):
        name, _, _ = photos.save(
            self.upload(self.make_image(400, 400, "PNG"), "shot.png")
        )
        with Image.open(os.path.join(photos.photo_dir(), name)) as stored:
            self.assertEqual(stored.format, "JPEG")

    def test_a_disguised_file_is_refused(self):
        with self.assertRaises(photos.PhotoError):
            photos.save(self.upload(b"#!/bin/sh\nrm -rf /\n", "evil.jpg"))

    def test_html_with_an_image_name_is_refused(self):
        with self.assertRaises(photos.PhotoError):
            photos.save(self.upload(b"<svg onload=alert(1)>", "x.jpg"))

    def test_empty_file_is_refused(self):
        with self.assertRaises(photos.PhotoError):
            photos.save(self.upload(b"", "nothing.jpg"))

    def test_oversized_file_is_refused_before_decoding(self):
        with self.assertRaises(photos.PhotoError):
            photos.save(self.upload(b"\xff" * (photos.MAX_UPLOAD_BYTES + 10)))

    def test_stored_names_are_generated_not_taken_from_the_upload(self):
        name, _, _ = photos.save(
            self.upload(self.make_image(50, 50), "../../etc/passwd.jpg")
        )
        self.assertTrue(photos.FILENAME_RE.match(name))
        self.assertNotIn("passwd", name)
        self.assertNotIn("/", name)

    def test_remove_ignores_names_it_did_not_generate(self):
        # Should never touch the filesystem, and never raise.
        photos.remove("../../../schema.sql")
        photos.remove("")
        photos.remove("not-a-real-name.jpg")
        self.assertTrue(os.path.exists("schema.sql"))


class MigrationTests(unittest.TestCase):
    """The site may already be live when a schema change ships, and schema.sql
    only ever CREATEs IF NOT EXISTS, so db.migrate is the only thing standing
    between a new column and a broken deploy."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.previous = os.environ.get("DATA_DIR")
        os.environ["DATA_DIR"] = self.dir

    def tearDown(self):
        if self.previous is None:
            os.environ.pop("DATA_DIR", None)
        else:
            os.environ["DATA_DIR"] = self.previous
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_old_author_columns_are_migrated_without_losing_recipes(self):
        conn = db.connect()
        conn.executescript(
            """
            CREATE TABLE recipes (
                id INTEGER PRIMARY KEY,
                slug TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                author_id INTEGER,
                author_name TEXT NOT NULL
            );
            """
        )
        conn.execute(
            "INSERT INTO recipes(slug, title, author_id, author_name) "
            "VALUES('pie', 'Apple Pie', 7, 'Barb Valentine')"
        )
        conn.commit()

        db.migrate(conn)
        conn.commit()

        columns = db.columns(conn, "recipes")
        self.assertIn("submitted_by_id", columns)
        self.assertIn("submitted_by_name", columns)
        self.assertIn("credited_to", columns)
        self.assertNotIn("author_id", columns)

        row = conn.execute("SELECT * FROM recipes").fetchone()
        self.assertEqual(row["title"], "Apple Pie")
        self.assertEqual(row["submitted_by_id"], 7)
        self.assertEqual(row["submitted_by_name"], "Barb Valentine")
        self.assertEqual(row["credited_to"], "")
        conn.close()

    def test_migrate_is_safe_to_run_twice(self):
        db.init()
        conn = db.connect()
        db.migrate(conn)          # already current; must be a no-op
        db.migrate(conn)
        conn.commit()
        self.assertIn("credited_to", db.columns(conn, "recipes"))
        conn.close()


class OutboundRequestTests(unittest.TestCase):
    """Every outbound API call must introduce itself.

    urllib defaults to "Python-urllib/3.x", which Cloudflare blocks with
    "error code: 1010" before the request reaches the API behind it. That cost
    a live site every one of its sign-in emails, and it fails invisibly: the
    provider never sees the request, so their logs show nothing at all.
    """

    def _captured_request(self, send):
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["headers"] = {k.lower(): v for k, v in request.header_items()}
            raise urllib.error.URLError("stopped before the network")

        original = urllib.request.urlopen
        urllib.request.urlopen = fake_urlopen
        try:
            send()
        except Exception:                       # noqa: BLE001 - only headers matter
            pass
        finally:
            urllib.request.urlopen = original
        return captured.get("headers", {})

    def test_resend_sends_a_real_user_agent(self):
        os.environ["MAIL_PROVIDER"] = "resend"
        os.environ["RESEND_API_KEY"] = "re_test"
        try:
            headers = self._captured_request(
                lambda: mailer._send_resend("a@example.com", "s", "b")
            )
        finally:
            os.environ.pop("MAIL_PROVIDER", None)
            os.environ.pop("RESEND_API_KEY", None)
        agent = headers.get("user-agent", "")
        self.assertTrue(agent, "no User-Agent was sent")
        self.assertNotIn("python-urllib", agent.lower())

    def test_moderation_api_sends_a_real_user_agent(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-test"
        try:
            headers = self._captured_request(
                lambda: moderation.ai_review("some text", "comment")
            )
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        agent = headers.get("user-agent", "")
        self.assertTrue(agent, "no User-Agent was sent")
        self.assertNotIn("python-urllib", agent.lower())


class CreditTests(unittest.TestCase):

    def test_family_names_are_accepted(self):
        for name in ["Barb Valentine", "Aunt Ruth", "Grandma", "Jean-Luc O'Brien"]:
            self.assertEqual(moderation.check_credit(name), name)

    def test_blank_is_allowed_because_the_credit_is_optional(self):
        self.assertEqual(moderation.check_credit(""), "")
        self.assertEqual(moderation.check_credit(None), "")

    def test_junk_is_rejected(self):
        for bad in ["x", "a" * 61, "<script>", "call 555-1234"]:
            with self.assertRaises(moderation.Rejected):
                moderation.check_credit(bad)


if __name__ == "__main__":
    unittest.main()
