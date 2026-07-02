import * as pdfjsLib from
    "https://cdn.jsdelivr.net/npm/pdfjs-dist@6.1.200/build/pdf.mjs";

pdfjsLib.GlobalWorkerOptions.workerSrc =
    "https://cdn.jsdelivr.net/npm/pdfjs-dist@6.1.200/build/pdf.worker.mjs";


const reader = document.getElementById("reader");
const pdfUrl = reader.dataset.pdfUrl;

const canvas = document.getElementById("pdf-canvas");
const context = canvas.getContext("2d");

const pageCounter = document.getElementById("page-counter");
const previousButton = document.getElementById("previous-button");
const nextButton = document.getElementById("next-button");


let pdf;
let currentPage = 1;
let isRendering = false;


async function renderPage(pageNumber) {
    if (isRendering) {
        return;
    }

    isRendering = true;

    previousButton.disabled = true;
    nextButton.disabled = true;

    const page = await pdf.getPage(pageNumber);

    // Higher logical resolution for sharper text.
    const scale = 2;

    // Makes it sharp on Windows display scaling / high-DPI screens.
    const outputScale = window.devicePixelRatio || 1;

    const viewport = page.getViewport({ scale });

    // Real internal canvas resolution.
    canvas.width = Math.floor(viewport.width * outputScale);
    canvas.height = Math.floor(viewport.height * outputScale);

    // Visual size on the page.
    canvas.style.width = `${Math.floor(viewport.width)}px`;
    canvas.style.height = `${Math.floor(viewport.height)}px`;

    await page.render({
        canvasContext: context,
        viewport: viewport,

        // PDF.js draws extra pixels internally, so text stays crisp.
        transform: outputScale !== 1
            ? [outputScale, 0, 0, outputScale, 0, 0]
            : null
    }).promise;

    pageCounter.textContent = `Page ${currentPage} / ${pdf.numPages}`;

    previousButton.disabled = currentPage === 1;
    nextButton.disabled = currentPage === pdf.numPages;

    isRendering = false;
}

previousButton.addEventListener("click", async () => {
    if (currentPage > 1) {
        currentPage--;
        await renderPage(currentPage);
    }
});


nextButton.addEventListener("click", async () => {
    if (currentPage < pdf.numPages) {
        currentPage++;
        await renderPage(currentPage);
    }
});


async function startReader() {
    try {
        const loadingTask = pdfjsLib.getDocument({ url: pdfUrl });

        pdf = await loadingTask.promise;

        await renderPage(currentPage);
    } catch (error) {
        console.error(error);
        pageCounter.textContent = "Could not load the PDF.";
    }
}


startReader();