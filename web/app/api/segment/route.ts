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
    const response = await fetch(`${PROCESSING_SERVICE_URL}/segment`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    const text = await response.text();
    const contentType = response.headers.get("content-type") ?? "application/json";

    if (!response.ok) {
      return new NextResponse(
        text || JSON.stringify({ error: "El servicio de procesamiento devolvio un error." }),
        {
          status: response.status,
          headers: { "Content-Type": contentType },
        },
      );
    }

    return new NextResponse(text, {
      status: 200,
      headers: { "Content-Type": contentType },
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
