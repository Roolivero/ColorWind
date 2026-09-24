import { NextRequest, NextResponse } from "next/server";

const PROCESSING_SERVICE_URL =
  process.env.PROCESSING_SERVICE_URL ?? "http://127.0.0.1:8000";

export async function POST(request: NextRequest) {
  let body: unknown;

  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "El pedido no es JSON valido." }, { status: 400 });
  }

  try {
    const response = await fetch(`${PROCESSING_SERVICE_URL}/export/pdf`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    if (!response.ok) {
      const text = await response.text();
      return new NextResponse(
        text || JSON.stringify({ error: "El servicio de procesamiento devolvio un error." }),
        {
          status: response.status,
          headers: { "Content-Type": response.headers.get("content-type") ?? "application/json" },
        },
      );
    }

    const bytes = await response.arrayBuffer();
    return new NextResponse(bytes, {
      status: 200,
      headers: {
        "Content-Type": "application/pdf",
        "Content-Disposition":
          response.headers.get("content-disposition") ?? 'attachment; filename="colorwind.pdf"',
        "X-Generation-Seconds": response.headers.get("x-generation-seconds") ?? "",
      },
    });
  } catch {
    return NextResponse.json(
      {
        error:
          "No se pudo conectar con el servicio de procesamiento. Verifica que uvicorn este corriendo en http://127.0.0.1:8000.",
      },
      { status: 502 },
    );
  }
}
