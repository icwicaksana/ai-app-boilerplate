---
name: nama-skill
description: satu kalimat menjelaskan prosedur ini (10-200 karakter)
when_to_use: kapan agen harus memuat skill ini (5-200 karakter)
tags: [contoh, tag]
---

# Judul Prosedur

Tulis prosedur di sini sebagai langkah bernomor. Aturan menulis:

1. Satu skill = satu topik. Kalau topiknya melebar, pecah jadi dua skill.
2. `description` adalah satu-satunya yang dilihat model di indeks — tulis
   dengan kata yang sama seperti yang akan dipakai user saat bertanya.
3. `when_to_use` harus konkret ("user menanyakan garansi"), bukan umum
   ("membantu user").
4. Detail panjang (>20.000 karakter total) pindah ke berkas `references/`
   dan rujuk dari sini; body ini hanya berisi ringkasan + pointer.
5. Jangan tulis instruksi yang mengubah aturan sistem (mis. "abaikan aturan
   di atas") — body skill adalah DATA, bukan instruksi.
