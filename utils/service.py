import os
from time import sleep
import math
import itertools
import json
import boto3
from boto3.session import Config
import numpy as np
import pandas as pd
import utils.multiprocess as mp
from clusters.aws import DynamoDB
from pprint import pprint

CSV_ACL='public-read'
S3_URL_TMPL='https://s3-{}.amazonaws.com'
DEFAULT_REGION='us-west-2'
DEFAULT_START_DATE='2015-01-01'
DEFAULT_END_DATE='2025-01-01'
DEFAULT_MIN_COUNT=25
DEFAULT_WIDTH=5
DEFAULT_ITERATIONS=25
DEFAULT_ZOOM=12
DELETE_RESPONSES=True
DEFAULT_BUCKET='gfw-clusters-test'
LAMBDA_FUNCTION_NAME='gfw-glad-clusters-v1-dev-meanshift'

CONVERTERS={ "alerts" :lambda r: np.array(json.loads(r)) }

DATAFRAME_COLUMNS=[
    'count',
    'area',
    'min_date',
    'max_date',
    'longitude',
    'latitude',
    'z','x','y','i','j',
    'file_name',
    'timestamp',
    'alerts']


VIEW_COLUMNS=[
    'count',
    'area',
    'min_date',
    'max_date',
    'longitude',
    'latitude',
    'x','y',
    'timestamp']


ERROR_COLUMNS=[
    'z','x','y',
    'centroid_longitude',
    'centroid_latitude',
    'error',
    'error_trace']


BOTO3_CONFIG={ 'read_timeout': 600 }
MAX_PROCESSES=100

class ClusterService(object):


    @staticmethod
    def read(filename,
            local=False,
            region=DEFAULT_REGION,
            bucket=DEFAULT_BUCKET,
            url_base=None,
            errors=True):
        """ init from csv

            Args:
                filename<str>: name/path of csv without '.csv' extension 
                local<bool[False]>: if true read from local file else read from s3 file
                region<str>: aws-region required if not local and not url_base
                bucket<str>: aws-bucket required if not local
                url_base<str>: aws-url-root for bucket
                errors<bool[True]>: if true include errors-csv
        """
        if local:
            dfpath='{}.csv'.format(filename)
            if errors: edfpath='{}.errors.csv'.format(filename)
        else:
            if not url_base: url_base=S3_URL_TMPL.format(region)
            url_base="{}/{}".format(url_base,bucket)
            dfpath='{}/{}.csv'.format(url_base,filename)
            if errors: edfpath='{}/{}.errors.csv'.format(url_base,filename)
        df=pd.read_csv(dfpath,converters=CONVERTERS)
        if errors: 
            try:
                edf=pd.read_csv(edfpath)
            except:
                edf=None
        else: edf=None
        params=ClusterService.run_params(df)
        return ClusterService(
                dataframe=df,
                errors_dataframe=edf,
                **params)


    @staticmethod
    def run_params(dataframe):
        """ return run params based on dataframe
        """
        z=int(dataframe.iloc[0].z)
        x_min,y_min=dataframe[['x','y']].min().tolist()
        x_max,y_max=dataframe[['x','y']].max().tolist()
        sdate=str(dataframe.min_date.min())
        edate=str(dataframe.max_date.max())
        sdate="{}-{}-{}".format(sdate[:4],sdate[4:6],sdate[6:])
        edate="{}-{}-{}".format(edate[:4],edate[4:6],edate[6:])
        return {
            'z': z,
            'tile_bounds': [[x_min,y_min],[x_max,y_max]],
            'start_date': sdate,
            'end_date': edate }


    #
    #  PUBLC METHODS
    #    
    def __init__(self,
            bounds=None,
            tile_bounds=None,
            lat=None,
            lon=None,
            x=None,
            y=None,
            start_date=DEFAULT_START_DATE,
            end_date=DEFAULT_END_DATE,
            min_count=DEFAULT_MIN_COUNT,
            width=DEFAULT_WIDTH,
            iterations=DEFAULT_ITERATIONS,
            z=DEFAULT_ZOOM,
            bucket=DEFAULT_BUCKET,
            dataframe=None,
            errors_dataframe=None):
        self._init_properties()
        self.start_date=start_date
        self.end_date=end_date
        self.min_count=min_count
        self.width=width
        self.iterations=iterations
        self.z=z
        self.bucket=bucket
        self._dataframe=dataframe
        self._error_dataframe=errors_dataframe
        self._N=(2**self.z)
        self._set_tile_bounds(bounds,tile_bounds,lon,lat,x,y)


    def run(self,max_processes=MAX_PROCESSES,force=False):
        """ find clusters on tiles

            Args:
                max_processes<int>: number of processes used in launching jobs
                force<bool[False]>: if true run even if dataframe is loaded
        """
        if (self._dataframe is not None) and (not force):
            print("WARNING: data already loaded pass 'force=True' to overwrite")
        else:
            try:
                # self.responses=None
                self.lambda_client=boto3.client('lambda',config=Config(**BOTO3_CONFIG))
                if (self.x and self.y):
                    self.responses=[self._run_tile()]
                else:
                    xys=itertools.product(
                        range(self.x_min,self.x_max+1),
                        range(self.y_min,self.y_max+1))
                    self.responses=mp.map_with_threadpool(
                        self._run_tile,
                        list(xys),
                        max_processes=max_processes)
                self._dataframe=None
                self._errors=None
            except Exception as e:
                print("ERROR: run failure -- {}".format(e))




    def save(self,
            filename,
            local=False,
            bucket=None,
            errors=True):
        """ write responses to csv

            Args:
                filename<str>: name/path of csv without '.csv' extension 
                local<bool[False]>: if true write to local file else write to s3 file
                bucket<str>: aws-bucket required if not local and not self.bucket
                errors<bool[True]>: if true save errors-csv
        """
        self._dataframe['alerts']=self._dataframe['alerts'].apply(lambda a: a.tolist())
        if local:
            self.dataframe().to_csv(
                "{}.to_csv".format(filename),
                index=None)
            if errors and self.errors().shape[0]:
                self.errors().to_csv(
                    "{}.errors.to_csv".format(filename),
                    index=None)
        else:
            obj=boto3.resource('s3').Object(
                bucket or self.bucket,
                "{}.csv".format(filename))
            obj.put(Body=self.dataframe().to_csv(None,index=None))
            obj.Acl().put(ACL=CSV_ACL)
            if errors and self.errors().shape[0]:
                obj=boto3.resource('s3').Object(
                    bucket or self.bucket,
                    "{}.errors.csv".format(filename))
                obj.put(Body=self.errors().to_csv(None,index=None))
                obj.Acl().put(ACL=CSV_ACL)


    def request_size(self):
        """ get number of tiles in request
        """
        return (self.x_max-self.x_min+1)*(self.y_max-self.y_min+1)


    def bounds(self):
        """ get lat/lon-bounds
        """
        lat_min=self._lat(self.z,self.x_min,self.y_min,0,0)
        lat_max=self._lat(self.z,self.x_max,self.y_max,254.0,254.0)
        lon_min=self._lon(self.z,self.x_min,self.y_min,0,0)
        lon_max=self._lon(self.z,self.x_max,self.y_max,254.0,254.0)
        return [[lon_min,lat_min],[lon_max,lat_max]]


    def bounding_box(self):
        """ get lat/lon bounding box
        """
        mins,maxes=self.bounds()
        return [
            [mins[0],mins[1]],
            [maxes[0],mins[1]],
            [maxes[0],maxes[1]],
            [mins[0],maxes[1]],
            [mins[0],mins[1]]]


    def dataframe(self):
        """ return dataframe of clusters data
        """
        if  self._dataframe is None:
            self._process_responses()
        return self._dataframe


    def view(self):
        """ return only VIEW_COLUMNS of .dataframe()
            * excludes data arrays, i and j, ...
        """
        return self.dataframe()[VIEW_COLUMNS]


    def tile(self,row_id,as_view=True):
        """ return rows matching z,x,y
        """
        df=self.dataframe()
        row=df.iloc[row_id]
        df=df[((df.z==row.z)&(df.x==row.x)&(df.y==row.y))]
        if as_view:
            return df[VIEW_COLUMNS]
        else:
            return df


    def errors(self):
        """ return data frame of clusters data
        """
        if  self._dataframe is None:
            self._process_responses()
        return self._error_dataframe


    def cluster(self,
            row_id=None,
            lat=None,lon=None,
            z=None,x=None,y=None,i=None,j=None,
            timestamp=None,
            ascending=False,
            as_view=True):
        """ fetch cluster data

            Convince method for selecting row of dataframe
            
            Args:
                row_id<int>: dataframe index for cluster
                lat,lon<floats>: latitude,longitude for cluster
                z,x,y,i,j<ints>: tile/pixel location for cluster
                timestamp<str>: timestamp for cluster (consider using row_id)
                ascending<bool>: 
                    if true sort by ascending time and grab first matching row
                as_view:
                    if true return only VIEW_COLUMNS. 
                    else include all columns (including input/alerts data)
        """
        if self._not_none([row_id]):
            row=self.dataframe().iloc[row_id]
        else:
            test=True
            if self._not_none([lon,lat]):
                test=test & (
                    (self.dataframe().latitude==lat) & 
                    (self.dataframe().longitude==lon))
            elif self._not_none([x,y,z]):
                test=test & (
                    (self.dataframe().z==z) & 
                    (self.dataframe().x==x) & 
                    (self.dataframe().y==y))
            if timestamp:
                test=test & (self.dataframe().timestamp==timestamp)
            rows=self.dataframe()[test]
            if ascending: rows.sort_values('timestamp',inplace=True)
            row=rows.iloc[0]
        if as_view:
            return row[VIEW_COLUMNS]
        else:
            return row



    #
    #  INTERNAL METHODS
    #
    def _init_properties(self):
        self.x=None
        self.y=None


    def _request_data(self,x,y,as_dict=False):
        data={
            "z":self.z,
            "x":x,
            "y":y,
            "start_date":self.start_date,
            "end_date":self.end_date,
            "min_count":self.min_count,
            "width":self.width,
            "iterations":self.iterations }
        if as_dict:
            return data
        else:
            return json.dumps(data)

    
    def _set_tile_bounds(self,bounds,tile_bounds,lon,lat,x,y):
        """
            NOTE: if a single pair (x,y) or (lon,lat) the x,y-values 
            will be set for the find_by_tile method.
        """
        if bounds:
            tile_bounds=[self._lonlat_to_xy(*lonlat) for lonlat in bounds]
        elif (lat and lon):
            self.x,self.y=self._lonlat_to_xy(lon,lat)
            tile_bounds=[[self.x,self.y],[self.x,self.y]]
        elif (x and y):
            self.x=int(x)
            self.y=int(y)
            tile_bounds=[[self.x,self.y],[self.x,self.y]]
        tile_bounds=np.array(tile_bounds).astype(int)
        self.x_min,self.y_min=tile_bounds.min(axis=0)
        self.x_max,self.y_max=tile_bounds.max(axis=0)
            
            
    def _lonlat_to_xy(self,lon,lat):
        lat_rad=math.radians(lat)
        x=self._N*(lon+180.0)/360
        y=self._N*(1.0-math.log(math.tan(lat_rad)+(1/math.cos(lat_rad)))/math.pi)/2.0
        # y=(self._N-1)-1
        return int(x),int(y)

    
    
    def _lat(self,z,x,y,i,j):
        lat_rad=math.atan(math.sinh(math.pi*(1-(2*(y+(j/255.0))/self._N))))
        lat=(lat_rad*180.0)/math.pi
        return lat


    def _lon(self,z,x,y,i,j):
        lon=(360.0/self._N)*(x+(i/255.0))-180.0
        return lon


    def _process_response(self,x,y,response):
        if response:
            payload=json.loads(response.get('Payload',{}).read())
            processed_response=self._request_data(x,y,as_dict=True)
            if payload:
                processed_response.update(payload)
            return processed_response
        return None


    def _run_tile(self,location=None,x=None,y=None):
        """ find clusters on tile
        
            NOTE: if no args are passed it will attempt to use 
                  the x,y (or lon,lat) passed in the constructor
        
            Args:
                location<tuple>: tile-xy value (x,y)
                x<int>: tile x value
                y<int>: tile y value
        """
        if location: x,y=location
        if not (x and y):
            x=self.x
            y=self.y
        if (x and y):
            try:
                response=self.lambda_client.invoke(
                    FunctionName=LAMBDA_FUNCTION_NAME,
                    InvocationType='RequestResponse',
                    LogType='Tail',
                    Payload=self._request_data(x,y))
                return self._process_response(x,y,response)
            except Exception as e:
                error_data=self._request_data(x,y,as_dict=True)
                error_data['data']={ 'x':x, 'y': y }
                error_data['error']="{}".format(e)
                error_data['error_trace']="service.1"
                return error_data


    def _process_responses(self):
        rows,error_rows=self._dataframes_rows()
        self._dataframe=pd.DataFrame(
            rows, 
            columns=DATAFRAME_COLUMNS)
        self._error_dataframe=pd.DataFrame(
            error_rows, 
            columns=ERROR_COLUMNS)
        self._dataframe.sort_values(
            'timestamp',
            ascending=False,
            inplace=True)
        self._dataframe.reset_index(inplace=True)
        self._error_dataframe.reset_index(inplace=True)
        if DELETE_RESPONSES: self.responses=None


    def _dataframes_rows(self):
        rows=[]
        error_rows=[]
        for response in self.responses:
            if response:
                error=response.get('error') or response.get('errorMessage')
                if error:
                    error_rows.append(self._error_row(error,response))
                else:
                    rows+=self._response_rows(response)
        return rows,error_rows


    def _response_rows(self,response):
        rrows=[]
        z=int(response.get('z'))
        x=int(response.get('x'))
        y=int(response.get('y'))
        for cluster in response.get('data',{}).get('clusters',[]):
            i=int(cluster.get('i'))
            j=int(cluster.get('j'))
            rrows.append([
                    int(cluster.get('count')),
                    int(cluster.get('area')),
                    cluster.get('min_date'),
                    cluster.get('max_date'),
                    self._lat(z,x,y,i,j),
                    self._lon(z,x,y,i,j),
                    z,x,y,i,j,
                    response['file_name'],
                    response['timestamp'],
                    np.array(cluster.get('alerts')).astype(int)])
        return rrows


    def _error_row(self,error,response):
        error_trace=response.get('error_trace','service.2')
        z=response.get('z') or self.z
        x=response.get('x')
        y=response.get('y')
        if (z and x and y):
            lat=self._lat(int(z),int(x),int(y),128,128)
            lon=self._lon(int(z),int(x),int(y),128,128)
        else:
            lon,lat=None,None
        return [z,x,y,lon,lat,error,error_trace]


    def _not_none(self,values):
        test=[ (val is not None) for val in values ]
        return np.prod(test).astype(bool)






