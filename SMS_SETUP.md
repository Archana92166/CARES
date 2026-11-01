# CARES automatic guardian SMS setup

CARES sends a personalized guardian alert automatically when the existing
risk engine reports a transition into a new HIGH-risk episode. Consecutive
HIGH readings do not create duplicate alerts; a later HIGH after recovery can
create a new alert.

## Enable TextBee

1. Copy `.env.example` to `.env` in the project root.
2. Set `CARES_SMS_PROVIDER=textbee`.
3. Set `TEXTBEE_API_KEY` to the fresh API key from your TextBee account.
4. Optionally set `TEXTBEE_DEVICE_ID` to your registered Android device ID.
   If omitted, TextBee chooses the default/most recently active enabled device.
5. Keep the TextBee app running with SMS permission enabled and the phone
   connected to the internet. Ensure the SIM can send SMS.
6. Start the CARES backend normally.

Do not commit or share `.env`. The project `.gitignore` excludes it.
Never put the API key in frontend JavaScript or send it in chat.

## Guardian phone numbers

Guardian numbers must be stored in international E.164 format, for example
`+919876543210`. Spaces, brackets, dots and hyphens are removed automatically
before sending. Ambiguous local-format numbers are rejected rather than
guessed.

## Notification status

- `MOCK_SENT`: local simulation only; no real SMS was sent.
- `PENDING`: TextBee accepted/queued the request. This is not proof of delivery.
- `FAILED`: configuration, network, TextBee, phone-number or provider error.
- `UNAVAILABLE`: no guardian contact is registered.

For TextBee sends, CARES stores the returned `smsBatchId`, provider acceptance,
recipient and warning details in the guardian action metadata. Delivery status
can be checked using the TextBee batch endpoint; this project version does not
yet poll that endpoint automatically or claim delivery without confirmation.

## Safe testing

The default provider is `mock`. The test suite also forces mock mode unless a
test explicitly opts in while replacing the HTTP call with a fake response.
No real TextBee message is sent by the automated tests.

## TextBee API

The integration uses:
`POST https://api.textbee.dev/api/v1/gateway/send-sms`

TextBee API acceptance is not delivery. The phone must receive the queued
request and send it through its SIM; carrier delivery reports may or may not
be available.
