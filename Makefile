default: local

clean:
	rm -rf _bundle
	rm -rf _bundle.zip

env.json:
	echo '{"ApiLambda": {"SQUARE_TOKEN": "", "SQUARE_SIGNATURE_KEY": "", "QUICKBOOKS_ENVIRONMENT": "sandbox", "QUICKBOOKS_CLIENT_ID": "", "QUICKBOOKS_CLIENT_SECRET": "", "QUICKBOOKS_COMPANY_ID": "", "QUICKBOOKS_ACCESS_TOKEN": "", "QUICKBOOKS_REFRESH_TOKEN": ""}}' > env.json

_bundle.zip: clean
	poetry bundle venv _bundle/ --without dev --clear --platform manylinux_2_39_x86_64 --python /usr/bin/python
	cd _bundle/lib/python3.12/site-packages/; zip -r ../../../../_bundle.zip .

local: _bundle.zip env.json
	sam local start-api --region us-east-1 --profile gatech_771971951923_Shibboleth-fulladmin_credfile --docker-network host --env-vars env.json

invoke: _bundle.zip env.json
	sam local invoke --region us-east-1 --profile gatech_771971951923_Shibboleth-fulladmin_credfile --docker-network host --env-vars env.json --event event.json
