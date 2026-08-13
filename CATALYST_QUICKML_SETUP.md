# Catalyst QuickML setup

This is the deferred setup checklist for enabling SplitLLM's AI expense parsing
and receipt scanning. The application is currently safe to run with
`LLM_ENABLED=false`.

## What credentials this backend actually needs

There is no single model API key in the current integration. QuickML calls use:

- a text-model endpoint URL;
- an optional vision-model endpoint URL for receipt scanning;
- the Catalyst organization ID;
- a Zoho OAuth Self Client ID and secret; and
- a long-lived OAuth refresh token with the `QuickML.deployment.READ` scope.

The backend exchanges the refresh token for a one-hour access token and caches
it. Never put the client secret or refresh token in Flutter, Git, or a public
configuration file.

## 1. Select the QuickML models and copy their API details

1. Sign in to Catalyst QuickML using the Zoho account that owns the SplitLLM
   organization.
2. Open **Generative AI -> LLM Serving -> Models**.
3. Open the text model to use for expense parsing. In **Model Details -> API
   Details**, copy its endpoint URL and exact model name.
4. Open a vision-language model for receipt scanning and copy its endpoint URL
   and exact model name. This can be omitted initially; receipt scanning will
   then fall back to the text endpoint, which only works if that endpoint
   accepts image input.
5. Copy the Catalyst organization ID. The current example configuration already
   contains the SplitLLM org ID; verify it against the console before deploying.

Do not assume the example model names are still present in QuickML. Use the
exact names and request examples shown in the current model's API Details pane.

Official reference: [QuickML LLM Serving](https://docs.catalyst.zoho.com/en/quickml/help/generative-ai/llm-serving/).

## 2. Create the Zoho OAuth Self Client

1. Open the Zoho API Console in the same data centre as the account. For the
   current India setup, use <https://api-console.zoho.in/>.
2. Choose **Get Started -> Self Client -> Create**.
3. Copy the generated Client ID and Client Secret.
4. In the Self Client, generate an authorization code with scope
   `QuickML.deployment.READ`. Use a duration long enough to complete the next
   step; the code is short-lived and single-use.
5. Exchange the code immediately for tokens, replacing every placeholder:

   ```sh
   curl --request POST 'https://accounts.zoho.in/oauth/v2/token' \
     --data-urlencode 'grant_type=authorization_code' \
     --data-urlencode 'client_id=YOUR_CLIENT_ID' \
     --data-urlencode 'client_secret=YOUR_CLIENT_SECRET' \
     --data-urlencode 'code=YOUR_ONE_TIME_CODE'
   ```

6. Save the returned `refresh_token`. The returned `access_token` expires; do
   not configure the app with it. If no refresh token is returned, generate a
   fresh authorization code and repeat the exchange.

The accounts host must match the Zoho data centre where the Self Client was
created (`accounts.zoho.in` for India, rather than blindly using `.com`).

Official references: [Zoho Self Client overview](https://www.zoho.com/developer/oauth/self-client/overview.html)
and [authorization-code flow](https://www.zoho.com/developer/oauth/self-client/authorization-code-flow.html).

## 3. Configure the backend

For local development, copy `.env.example` to `.env` and set:

```dotenv
LLM_ENABLED=true

QUICKML_LLM_ENDPOINT=https://PASTE-TEXT-ENDPOINT-HERE
QUICKML_LLM_MODEL=PASTE-EXACT-TEXT-MODEL-NAME
QUICKML_VLM_ENDPOINT=https://PASTE-VISION-ENDPOINT-HERE
QUICKML_VLM_MODEL=PASTE-EXACT-VISION-MODEL-NAME
QUICKML_ORG_ID=PASTE-CATALYST-ORG-ID

OAUTH_ACCOUNTS_URL=https://accounts.zoho.in
OAUTH_CLIENT_ID=PASTE-SELF-CLIENT-ID
OAUTH_CLIENT_SECRET=PASTE-SELF-CLIENT-SECRET
OAUTH_REFRESH_TOKEN=PASTE-REFRESH-TOKEN
```

For the current IBM Cloud Code Engine deployment, put the same values in the
untracked `ibm/secrets.env`, following `ibm/secrets.example.env`, and redeploy
using the documented IBM deployment script. The IBM Cloud API key used by the
deployment script is unrelated to QuickML model authentication.

The old AppSail configuration remains only for reference. If AppSail is used
again, set the values as service environment variables in the Catalyst console;
do not commit secrets into `catalyst/appsail/app-config.json`.

## 4. Enable and verify the feature

1. Restart or redeploy the backend after setting the environment variables.
2. In the SplitLLM admin settings, ensure **LLM enabled** is on. The database
   setting can override the environment default.
3. Sign in and test text parsing through the app, or call the authenticated
   `POST /llm/expense` endpoint with:

   ```json
   {
     "requestData": {
       "sentence": "Lunch was INR 900, paid by me, split equally with Alex"
     }
   }
   ```

4. Test receipt scanning separately through `POST /llm/receipt` or the app.
5. If OAuth fails, first check the data-centre host, scope, and refresh token.
   If the model call fails, compare the configured endpoint, model name, headers,
   and payload with the live integration example in QuickML's API Details pane.

## Security checklist

- Keep `.env` and `ibm/secrets.env` untracked.
- Store production values in the deployment platform's secret/environment
  configuration.
- Rotate the Self Client secret and refresh token if either is exposed.
- Grant only `QuickML.deployment.READ` unless another feature explicitly needs
  a broader scope.
