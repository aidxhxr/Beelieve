import { Helmet } from 'react-helmet-async';

import { HivesView } from 'src/sections/hives/view';

// ----------------------------------------------------------------------

export default function UserPage() {
  return (
    <>
      <Helmet>
        <title> Hives </title>
      </Helmet>

      <HivesView />
    </>
  );
}
