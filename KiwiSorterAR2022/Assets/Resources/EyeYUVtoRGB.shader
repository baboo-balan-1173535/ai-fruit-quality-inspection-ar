// EyeYUVtoRGB.shader  (in Resources/ so it's always included in the build)
// Converts the XReal Eye's YUV_420_888 planes (Y, U, V as Alpha8 textures)
// into a correct RGB image we can ReadPixels + EncodeToJPG for the server.
Shader "KiwiSorter/EyeYUVtoRGB"
{
    Properties
    {
        _MainTex ("Y", 2D) = "black" {}
        _UTex    ("U", 2D) = "black" {}
        _VTex    ("V", 2D) = "black" {}
    }
    SubShader
    {
        Tags { "RenderType" = "Opaque" }
        LOD 100
        Pass
        {
            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #include "UnityCG.cginc"

            struct appdata { float4 vertex : POSITION; float2 uv : TEXCOORD0; };
            struct v2f     { float2 uv : TEXCOORD0; float4 vertex : SV_POSITION; };

            sampler2D _MainTex;
            sampler2D _UTex;
            sampler2D _VTex;
            float4 _MainTex_ST;

            v2f vert(appdata v)
            {
                v2f o;
                o.vertex = UnityObjectToClipPos(v.vertex);
                o.uv = TRANSFORM_TEX(v.uv, _MainTex);
                return o;
            }

            fixed4 frag(v2f i) : SV_Target
            {
                float y = tex2D(_MainTex, i.uv).a;
                float u = tex2D(_UTex,    i.uv).a;
                float v = tex2D(_VTex,    i.uv).a;

                float r = y + 1.4022 * v - 0.7011;
                float g = y - 0.3456 * u - 0.7145 * v + 0.53005;
                float b = y + 1.7710 * u - 0.8855;

                // Output B,G,R order — the Eye pipeline expects R/B swapped here,
                // otherwise red fruit comes out blue.
                return fixed4(saturate(b), saturate(g), saturate(r), 1.0);
            }
            ENDCG
        }
    }
}
