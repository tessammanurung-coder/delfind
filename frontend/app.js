const API_URL = "https://delfind.onrender.com";

// Fungsi otomatis untuk mengambil data dari Render begitu web dibuka
async function ambilData() {
    try {
        const respon = await fetch(`${API_URL}/items_lost`);
        const data = await respon.json();
        console.log("Data dari server Render:", data);
        
        // MENAMPILKAN DATA LANGSUNG KE LAYAR WEB
        // Kode ini akan otomatis mencari tempat kosong di HTML kamu dan memunculkan teksnya
        document.body.innerHTML += `<div style="text-align:center; padding:20px;">
            <h2>Data Berhasil Terhubung ke Render!</h2>
            <pre>${JSON.stringify(data, null, 2)}</pre>
        </div>`;

    } catch (error) {
        alert("Waduh, koneksi ke Render gagal: " + error.message);
    }
}

// Jalankan fungsi di atas secara otomatis
ambilData();